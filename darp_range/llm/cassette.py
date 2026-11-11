"""Запись и воспроизведение живых траекторий (ревизия 4, NFR-2.3).

Seed у внешнего провайдера не гарантирует повтор: одна и та же команда с тем же seed даёт разные
исходы (наблюдение критика ревизии 3). Повтор живого прогона поэтому делается не повторным
сэмплированием, а воспроизведением записанных ответов модели:

- `RecordingTransport` оборачивает HTTP-транспорт `OpenAICompatibleBackend` и пишет в JSONL
  (кассету) на каждый запрос: порядковый номер, SHA-256 тела запроса, HTTP-статус и тело ответа
  (или текст ошибки транспорта). Заголовки (в т.ч. авторизация) и адрес сервера не пишутся.
- `ReplayTransport` отдаёт ответы по SHA-256 тела запроса (очередь на каждый хеш — для одинаковых
  запросов). Запрос, которого нет в кассете, — расхождение траектории: фиксируется как дефект
  воспроизводимости (`divergences`) и обрывает вызов (`ReplayDivergence`); сеть не используется.
- `cassette_header` — параметры модели (имя, seed, температура, лимит вывода, доп. поля тела),
  без которых тело запроса нельзя построить заново.

Детерминизм остальной системы (агент, политика, исполнитель, оценщик) — свойство кода полигона;
воспроизведение по кассете проверяет его на живых ответах.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Mapping

from .types import LLMUnavailable

Transport = Callable[[str, Mapping[str, str], bytes, float], "tuple[int, bytes]"]
CASSETTE_VERSION = 1


class ReplayDivergence(LLMUnavailable):
    """Запрос при воспроизведении не совпал ни с одной записью кассеты."""


def body_sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def cassette_header(settings: Any, *, role: str = "") -> dict[str, Any]:
    return {"kind": "header", "version": CASSETTE_VERSION, "role": role, "model": settings.model,
            "seed": settings.seed, "temperature": settings.temperature,
            "max_output_tokens": settings.max_output_tokens,
            "extra_body": dict(getattr(settings, "extra_body", {}) or {})}


class RecordingTransport:
    def __init__(self, inner: Transport, path: str | Path, header: Mapping[str, Any] | None = None):
        self.inner = inner
        self.path = Path(path)
        self.seq = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if header is not None and not self.path.exists():
            self._write(dict(header))

    def _write(self, rec: Mapping[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes,
                 timeout_s: float) -> tuple[int, bytes]:
        self.seq += 1
        rec: dict[str, Any] = {"kind": "call", "seq": self.seq, "req_sha256": body_sha(body),
                               "req_bytes": len(body)}
        try:
            status, raw = self.inner(url, headers, body, timeout_s)
        except LLMUnavailable as exc:
            rec["error"] = str(exc)[:500]
            self._write(rec)
            raise
        rec["status"] = status
        rec["response"] = raw.decode("utf-8", "replace") if raw else ""
        self._write(rec)
        return status, raw


def load_cassette(path: str | Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    header, calls = None, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") == "header" and header is None:
            header = r
        elif r.get("kind") == "call":
            calls.append(r)
    return header, calls


class ReplayTransport:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.header, calls = load_cassette(self.path)
        self.total = len(calls)
        self._by_sha: dict[str, deque] = defaultdict(deque)
        for c in calls:
            self._by_sha[c["req_sha256"]].append(c)
        self.served = 0
        self.divergences: list[dict[str, Any]] = []

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes,
                 timeout_s: float) -> tuple[int, bytes]:
        sha = body_sha(body)
        q = self._by_sha.get(sha)
        if not q:
            self.divergences.append({"request_index": self.served + len(self.divergences) + 1,
                                     "req_sha256": sha, "req_bytes": len(body)})
            raise ReplayDivergence(f"replay: запроса {sha[:12]} нет в кассете (расхождение)")
        rec = q.popleft()
        self.served += 1
        if "error" in rec:
            raise LLMUnavailable(rec["error"])
        return int(rec["status"]), (rec.get("response") or "").encode("utf-8")

    def report(self) -> dict[str, Any]:
        unused = sum(len(q) for q in self._by_sha.values())
        return {"cassette": str(self.path), "cassette_sha256": file_sha(self.path),
                "recorded_calls": self.total, "served": self.served, "unused": unused,
                "divergences": list(self.divergences),
                "exact": not self.divergences and unused == 0}


def file_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compare_cassettes(a: str | Path, b: str | Path) -> dict[str, Any]:
    """Два живых прогона одной команды: одинаковые тела запросов с разными ответами — прямое
    свидетельство недетерминизма провайдера; первое расхождение тел — точка расхождения траекторий."""
    _, ca = load_cassette(a)
    _, cb = load_cassette(b)
    first_div = next((i for i, (x, y) in enumerate(zip(ca, cb)) if x["req_sha256"] != y["req_sha256"]),
                     None)
    resp_a = {c["req_sha256"]: c.get("response", c.get("error")) for c in ca}
    same_req_diff_resp = sum(1 for c in cb if c["req_sha256"] in resp_a
                             and resp_a[c["req_sha256"]] != c.get("response", c.get("error")))
    return {"calls_a": len(ca), "calls_b": len(cb),
            "first_divergent_request": None if first_div is None else first_div + 1,
            "identical_request_prefix": first_div if first_div is not None else min(len(ca), len(cb)),
            "same_request_different_response": same_req_diff_resp}
