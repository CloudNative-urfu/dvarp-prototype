"""Журнал точных payload запросов атакующей модели (ревизия 9, payload-audit-invariance).

Брокер записывает каждый запрос атакующего процесса к модели: канонический JSON
`{"messages": [{role, content}, ...]}` и его sha256. Заголовков, URL, ключа, параметров
транспорта в записи нет и взяться неоткуда: брокер получает от процесса атакующего только
сообщения (`ipc.llm_request`). Журнал отделён от журнала событий защиты (`EventLog`):
другой файл, своя цепочка хешей.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from darp_range.repro import canonical_json, hash_obj
from darp_range.repro.hashing import sha256_bytes

GENESIS = "0" * 64


def payload_bytes(messages: Sequence[Mapping[str, Any]]) -> bytes:
    return canonical_json({"messages": [{"role": m["role"], "content": m["content"]}
                                        for m in messages]})


def payload_sha(messages: Sequence[Mapping[str, Any]]) -> str:
    return sha256_bytes(payload_bytes(messages))


class PayloadAudit:
    """Добавляющий журнал payload атакующей модели (в памяти и, если задан, в JSONL)."""

    def __init__(self, sink: str | Path | None = None):
        self.records: list[dict[str, Any]] = []
        self._prev = GENESIS
        self._sink = None
        if sink is not None:
            p = Path(sink)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._sink = p.open("a", encoding="utf-8")

    def record(self, messages: Sequence[Mapping[str, Any]], *, brief_id: str,
               episode_id: str | None, campaign: str) -> dict[str, Any]:
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        body = {"seq": len(self.records), "campaign": campaign, "brief_id": brief_id,
                "episode_id": episode_id, "sha256": payload_sha(msgs),
                "payload": {"messages": msgs}}
        rec = dict(body, prev=self._prev, h=hash_obj({"prev": self._prev, "body": body}))
        self._prev = rec["h"]
        self.records.append(rec)
        if self._sink is not None:
            self._sink.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            self._sink.flush()
        return rec

    def hashes(self) -> list[str]:
        return [r["sha256"] for r in self.records]

    def close(self) -> None:
        if self._sink is not None:
            self._sink.close()
            self._sink = None


def verify_audit(records: Sequence[Mapping[str, Any]]) -> list[str]:
    errs, prev = [], GENESIS
    for i, r in enumerate(records):
        body = {k: v for k, v in r.items() if k not in ("prev", "h")}
        if r.get("prev") != prev or hash_obj({"prev": prev, "body": body}) != r.get("h"):
            errs.append(f"запись {i}: цепочка нарушена")
        if payload_sha(r["payload"]["messages"]) != r.get("sha256"):
            errs.append(f"запись {i}: sha256 не совпадает с payload")
        prev = r.get("h")
    return errs
