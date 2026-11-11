"""Адаптер OSS-детектора инъекций protectai/deberta-v3-base-prompt-injection-v2 (ревизия 2, K2.4).

DeBERTa-v3-base (~184M, Apache-2.0, ≤ 8B — NFR-2.4/2.6), ONNX-экспорт из репозитория модели,
ревизия и SHA-256 файлов закреплены в `configs/oss/deberta-v3-base-prompt-injection-v2.lock.json`.
Исполняется локально на CPU (onnxruntime) — внутри контура, без сетевых вызовов.

Два режима одного и того же детектора (режим не входит в отпечаток плагина: детектор тот же):

- `onnx` (`DARP_OSS_MODE=onnx`) — живой вывод модели: токенизатор + ONNX-сессия, вероятность
  метки INJECTION; оценка, время CPU и задержка каждого текста дописываются в журнал записи
  (`DARP_OSS_RECORD`). Нужны веса (`scripts/oss_fetch.py`) и окружение с onnxruntime/tokenizers/
  numpy (`configs/oss/*.lock.json → runtime`); это ресурсная проверка, не офлайн-verify;
- `replay` (по умолчанию) — воспроизведение по кэшу оценок `corpus/oss_scores/…json`, который
  записан живым прогоном модели закреплённой ревизии (в кэше — sha256 весов и токенизатора,
  версии рантайма). Ключ — sha256 текста. Вызов начисляет записанные при живом выводе CPU-время
  и задержку (`GuardRuntime.charge_replayed`), иначе C_guard занижался бы на стоимость модели.
  Текста нет в кэше → `ScoreCacheMiss` → хост вернёт политике `status="error"`; основной
  прогон verify требует 0 промахов (иначе сравнение не засчитывается).

Порог 0.5 — по умолчанию карточки модели, на корпусе не подбирался. Сигнал: score ≥ порога →
`flag_signal` (по умолчанию `flag`), иначе `allow`; score передаётся политике.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import time
from pathlib import Path
from typing import Any

from darp_range.guard_contract import GuardInput, GuardPlugin, GuardRuntime, GuardSignal, register

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "configs" / "oss" / "deberta-v3-base-prompt-injection-v2.lock.json"


class ScoreCacheMiss(RuntimeError):
    """Текста нет в кэше оценок: офлайн-воспроизведение невозможно."""


class OssRuntimeUnavailable(RuntimeError):
    """Нет весов или рантайма ONNX в этом окружении."""


def text_key(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def load_lock() -> dict[str, Any]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


_CACHE: dict[str, Any] = {}
_SESSION: dict[str, Any] = {}


def _cache() -> dict[str, Any]:
    path = Path(os.environ.get("DARP_OSS_CACHE") or ROOT / load_lock()["score_cache"])
    key = str(path)
    if key not in _CACHE:
        if not path.exists():
            raise ScoreCacheMiss(f"кэш оценок {path} отсутствует")
        _CACHE.clear()
        _CACHE[key] = json.loads(path.read_text(encoding="utf-8"))
    return _CACHE[key]


def _onnx():
    """Ленивая загрузка ONNX-сессии (только в ресурсной среде)."""
    if "sess" in _SESSION:
        return _SESSION
    lock = load_lock()
    mdir = Path(os.environ.get("DARP_OSS_MODEL_DIR") or ROOT / lock["model_dir_default"])
    try:  # необязательные зависимости ресурсной среды: только динамический импорт (аудит NFR-2.5)
        importlib.import_module("numpy")
        ort = importlib.import_module("onnxruntime")
        Tokenizer = importlib.import_module("tokenizers").Tokenizer
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise OssRuntimeUnavailable(f"нет рантайма ONNX: {exc}") from exc
    for rel, sha in lock["files"].items():
        p = mdir / Path(rel).name
        if not p.exists():
            raise OssRuntimeUnavailable(f"нет файла весов {p} (scripts/oss_fetch.py)")
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != sha:
            raise OssRuntimeUnavailable(f"sha256 {p.name} не совпадает с lock")
    cfg = json.loads((mdir / "config.json").read_text())
    tok = Tokenizer.from_file(str(mdir / "tokenizer.json"))
    tok.enable_truncation(int(lock["max_tokens"]))
    so = ort.SessionOptions()
    so.intra_op_num_threads = int(lock["runtime"]["intra_op_num_threads"])
    sess = ort.InferenceSession(str(mdir / "model.onnx"), so, providers=["CPUExecutionProvider"])
    _SESSION.update(sess=sess, tok=tok, names={i.name for i in sess.get_inputs()},
                    inj=[int(k) for k, v in cfg["id2label"].items() if v.upper() == "INJECTION"][0],
                    runtime=f"onnxruntime {ort.__version__}")
    return _SESSION


def onnx_score(text: str) -> tuple[float, float, float]:
    """(score, cpu_ms, latency_ms) живого вывода модели."""
    np = importlib.import_module("numpy")
    s = _onnx()
    w0, c0 = time.perf_counter(), time.process_time()
    enc = s["tok"].encode(text or " ")
    feed = {"input_ids": np.array([enc.ids], dtype=np.int64),
            "attention_mask": np.array([enc.attention_mask], dtype=np.int64)}
    if "token_type_ids" in s["names"]:
        feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
    logits = s["sess"].run(None, {k: v for k, v in feed.items() if k in s["names"]})[0][0]
    ex = np.exp(logits - logits.max())
    score = float((ex / ex.sum())[s["inj"]])
    return score, (time.process_time() - c0) * 1000.0, (time.perf_counter() - w0) * 1000.0


def _record(text: str, score: float, cpu_ms: float, lat_ms: float) -> None:
    path = os.environ.get("DARP_OSS_RECORD")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": text_key(text), "text": text, "score": score,
                             "cpu_ms": cpu_ms, "latency_ms": lat_ms}, ensure_ascii=False) + "\n")


@register
class DebertaInjectionAdapter(GuardPlugin):
    plugin_id = "deberta-pi-v2"
    version = "90c9989"
    mechanism = "classifier"
    stages = ("input", "context")
    deployment = "self_hosted"
    license = "apache-2.0"
    origin = ("protectai/deberta-v3-base-prompt-injection-v2 @ 90c9989b (HF), ONNX export; "
              "AgentDojo's BERT-detector defense class (case/docs/methodology/agentdojo.md)")
    enabled_by_default = False
    default_params = {"threshold": 0.5, "flag_signal": "flag"}

    def _score(self, text: str, rt: GuardRuntime) -> float:
        mode = os.environ.get("DARP_OSS_MODE", "replay")
        if mode == "onnx":
            score, cpu, lat = onnx_score(text)
            _record(text, score, cpu, lat)
            return score
        row = _cache()["scores"].get(text_key(text))
        if row is None:
            raise ScoreCacheMiss(f"нет оценки для текста sha256={text_key(text)[:16]}")
        rt.charge_replayed(cpu_ms=row["cpu_ms"], latency_ms=row["latency_ms"])
        return float(row["score"])

    def inspect(self, inp: GuardInput, rt: GuardRuntime) -> GuardSignal:
        if inp.stage == "context":
            frags = list(inp.untrusted_fragments())
            if not frags:
                return GuardSignal("allow", score=0.0, reasons=("no untrusted context",))
            scored = [(i, self._score(c.text, rt)) for i, c in frags]
            hit = tuple(i for i, s in scored if s >= self.params["threshold"])
            top = max(s for _, s in scored)
            if hit:
                return GuardSignal(self.params["flag_signal"], score=top, targets=hit,
                                   labels=("prompt_injection",),
                                   reasons=(f"deberta-pi-v2: INJECTION p={top:.3f}",))
            return GuardSignal("allow", score=top, reasons=(f"deberta-pi-v2: SAFE p={top:.3f}",))
        s = self._score(inp.text or "", rt)
        if s >= self.params["threshold"]:
            return GuardSignal(self.params["flag_signal"], score=s, labels=("prompt_injection",),
                               reasons=(f"deberta-pi-v2: INJECTION p={s:.3f}",))
        return GuardSignal("allow", score=s, reasons=(f"deberta-pi-v2: SAFE p={s:.3f}",))
