"""Происхождение заданий атакующего и вычисляемый claim_level прогона (ревизия 9,
задача attack-provenance-gate).

Реестр `configs/isolation/provenance_registry.json` держит оценщик: хеш записи корпуса
(`MANIFEST.entry_hashes`) → класс происхождения из `PROVENANCES`. Все существующие корпуса
(v1, holdout_v2, heldout_v3, trusted_v7) зарегистрированы как `implementation-informed`:
их писал автор, знающий код защиты (holdout_v2 — перефраз той же Qwen по записям v1).
Тексты корпусов и их MANIFEST не меняются: реестр хранит sha256 каждого MANIFEST, тест
сверяет их.

Происхождение — запись брокера о брифе (`BriefProvenance`), а не поле, уходящее в процесс
атакующего: хеш записи корпуса зависит от GT и не должен попадать во вход атакующего
(инвариантность к GT).

`claim_level` вычисляется из фактов прогона, не объявляется:
- `isolated_black_box_candidate` — атакующий шёл в sandbox, все OS-пробы этого прогона
  прошли, research-mode выключен, ни одного брифа implementation-informed/research-feedback
  и нет ошибок IPC;
- `isolated_implementation_informed` — sandbox и пробы прошли, но брифы legacy или
  research-mode;
- `research` (ревизия 10) — sandbox и пробы прошли, но включён research-mode
  (привилегированная обратная связь): не выше `research`, никогда `isolated_*`;
- `in_process_research` — атакующий не в sandbox (в т. ч. откат) или любая проба провалена
  (`sandboxed` при этом записывается отдельно, журналы не смешиваются).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from darp_range.repro import sha256_file

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "configs" / "isolation" / "provenance_registry.json"
PROVENANCES = ("public", "implementation-informed", "blind-critic", "research-feedback")
NOT_BLIND = ("implementation-informed", "research-feedback")
CLAIM_LEVELS = ("isolated_black_box_candidate", "isolated_implementation_informed",
                "research", "in_process_research")
LEGACY_CORPORA = {"v2": "corpus", "holdout_v2": "corpus/holdout_v2",
                  "heldout_v3": "corpus/heldout_v3", "trusted_v7": "corpus/trusted_v7"}


class ProvenanceError(RuntimeError):
    """Бриф с недоказанным происхождением: прогон отказывается его использовать."""


def build_registry(root: Path = ROOT) -> dict[str, Any]:
    """Реестр для существующих корпусов: все атаки — implementation-informed."""
    entries: dict[str, dict[str, Any]] = {}
    corpora: dict[str, Any] = {}
    for name, rel in LEGACY_CORPORA.items():
        man_path = root / rel / "MANIFEST.json"
        man = json.loads(man_path.read_text(encoding="utf-8"))
        data = root / rel / man["file"]
        kinds = {}
        for line in data.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                kinds[e["id"]] = e["kind"]
        n = 0
        for eid, h in sorted(man["entry_hashes"].items()):
            if kinds.get(eid) != "attack":
                continue
            entries[h] = {"provenance": "implementation-informed", "corpus": name,
                          "entry_id": eid,
                          "reason": "legacy corpus written by the author with knowledge of the "
                                    "defence code (rev1-rev7); not blind"}
            n += 1
        corpora[name] = {"dir": rel, "manifest_sha256": sha256_file(man_path), "attacks": n}
    return {"schema": "darp-provenance-registry-1", "holder": "evaluator",
            "provenances": list(PROVENANCES), "corpora": corpora, "entries": entries}


def load_registry(path: str | Path = REGISTRY_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def manifest_drift(registry: Mapping[str, Any], root: Path = ROOT) -> list[str]:
    return [name for name, c in registry["corpora"].items()
            if sha256_file(root / c["dir"] / "MANIFEST.json") != c["manifest_sha256"]]


@dataclass(frozen=True)
class BriefProvenance:
    brief_id: str
    source_hash: str | None
    declared: str | None
    provenance: str
    registered: bool

    def to_json(self) -> dict:
        return asdict(self)


def resolve(brief_id: str, source_hash: str | None, registry: Mapping[str, Any], *,
            declared: str | None = None) -> BriefProvenance:
    """Происхождение брифа по реестру оценщика. Заявленный класс обязан совпасть с реестром;
    незаявленный и незарегистрированный бриф консервативно считается implementation-informed."""
    ent = registry["entries"].get(source_hash) if source_hash else None
    if declared is not None:
        if declared not in PROVENANCES:
            raise ProvenanceError(f"{brief_id}: provenance {declared!r} не из {PROVENANCES}")
        if ent is None:
            raise ProvenanceError(f"{brief_id}: заявлено {declared!r}, но хеш записи "
                                  f"{str(source_hash)[:12]} отсутствует в реестре оценщика")
        if ent["provenance"] != declared:
            raise ProvenanceError(f"{brief_id}: заявлено {declared!r}, в реестре "
                                  f"{ent['provenance']!r}")
    prov = ent["provenance"] if ent else "implementation-informed"
    return BriefProvenance(brief_id, source_hash, declared, prov, ent is not None)


def corpus_resolver(corpus_dir: str | None = None, *, registry: Mapping[str, Any] | None = None,
                    declared: Mapping[str, str] | None = None,
                    root: Path = ROOT) -> Callable[[Any], str]:
    """Функция `brief → provenance` для `AttackerFactory`: хеш записи берётся из MANIFEST
    корпуса прогона по `brief_id` (= id записи) — в брокере, не в процессе атакующего."""
    reg = registry if registry is not None else load_registry()
    man = json.loads((root / (corpus_dir or "corpus") / "MANIFEST.json").read_text(encoding="utf-8"))
    hashes = man["entry_hashes"]
    decl = dict(declared or {})
    log: list[BriefProvenance] = []

    def of(brief: Any) -> str:
        bp = resolve(brief.brief_id, hashes.get(brief.brief_id), reg,
                     declared=decl.get(brief.brief_id))
        log.append(bp)
        return bp.provenance

    of.log = log  # type: ignore[attr-defined]
    return of


def probes_passed(results: Sequence[Mapping[str, Any]] | None) -> bool:
    if not results:
        return False
    return all((r["actual"] == "DENY") if r["kind"] == "negative" else (r["actual"] == "PASS")
               for r in results)


def claim_level(*, sandboxed: bool, probe_results: Sequence[Mapping[str, Any]] | None,
                research_mode: bool, provenances: Iterable[str],
                ipc_errors: Sequence[str] = ()) -> dict[str, Any]:
    provs = sorted(set(provenances))
    ok_probes = probes_passed(probe_results)
    reasons = []
    if not sandboxed:
        reasons.append("attacker not sandboxed")
    if sandboxed and not ok_probes:
        reasons.append("OS probes of this run did not all pass")
    if ipc_errors:
        reasons.append(f"IPC protocol errors: {len(ipc_errors)}")
    if not sandboxed or not ok_probes:
        level = "in_process_research"
    elif research_mode:
        level = "research"
        reasons.append("research-mode feedback")
    elif not provs or any(p in NOT_BLIND or p not in PROVENANCES for p in provs) \
            or ipc_errors:
        level = "isolated_implementation_informed"
        if not provs:
            reasons.append("no briefs")
        if any(p in NOT_BLIND for p in provs):
            reasons.append(f"non-blind briefs: {[p for p in provs if p in NOT_BLIND]}")
    else:
        level = "isolated_black_box_candidate"
    return {"claim_level": level, "sandboxed": bool(sandboxed), "probes_passed": ok_probes,
            "research_mode": bool(research_mode), "provenances": provs,
            "ipc_errors": len(ipc_errors), "reasons": reasons}
