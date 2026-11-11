"""Повтор потока для обнаружения расхождений траекторий (NFR-2.3).

Поток прогоняется второй раз с теми же начальными условиями: тот же seed конфигурации,
тот же блок расписания, заново построенная из seed базовая БД и снимок в новом каталоге
(так проверяется и детерминизм генератора данных), новый журнал. Сравниваются:

- хеш снимка базовой БД (`scope="snapshot"`);
- состав и порядок эпизодов (`scope="episodes"`);
- траектория каждого эпизода — журнал без времени, latency и хеш-цепочки — через
  `repro.compare_trajectories` (первое расхождение, оба шага) (`scope="replay"`);
- итоговое состояние БД и вердикт оценщика каждого эпизода (`scope="state"`/`"verdict"`);
- итог общей очереди (`scope="queue"`).

Любое несовпадение — дефект воспроизводимости, а не результат: он пишется в журнал прогона
событием `runner.reproducibility_defect` и в результаты конфигурации; итог не «исправляется».
"""

from __future__ import annotations

from typing import Any

from darp_range.repro import compare_trajectories, hash_obj

from .flow import FlowResult


def compare_flows(orig: FlowResult, again: FlowResult) -> list[dict[str, Any]]:
    base = {"config": orig.config, "block": orig.block, "variant": orig.variant}
    out: list[dict[str, Any]] = []

    def defect(scope: str, **data: Any) -> None:
        out.append(dict(base, kind="reproducibility_defect", scope=scope, **data))

    if orig.baseline_hash != again.baseline_hash:
        defect("snapshot", sha256_a=orig.baseline_hash, sha256_b=again.baseline_hash)
    ids_a = [e.episode_id for e in orig.episodes]
    ids_b = [e.episode_id for e in again.episodes]
    if ids_a != ids_b:
        defect("episodes", only_a=sorted(set(ids_a) - set(ids_b)),
               only_b=sorted(set(ids_b) - set(ids_a)), order_differs=set(ids_a) == set(ids_b))
    eb = again.by_case()
    for a in orig.episodes:
        b = eb.get(a.episode_id)
        if b is None:
            continue
        d = compare_trajectories(a.trajectory, b.trajectory, episode_id=a.episode_id)
        if d is not None:
            out.append(dict(d, scope="replay", **base))
        if a.final_hash != b.final_hash:
            defect("state", episode_id=a.episode_id, sha256_a=a.final_hash, sha256_b=b.final_hash)
        if a.verdict.fingerprint() != b.verdict.fingerprint():
            defect("verdict", episode_id=a.episode_id,
                   outcome_a=a.verdict.outcome, outcome_b=b.verdict.outcome,
                   task_a=a.verdict.task_status, task_b=b.verdict.task_status)
    qa, qb = hash_obj(orig.queue.summary()), hash_obj(again.queue.summary())
    if qa != qb:
        defect("queue", sha256_a=qa, sha256_b=qb)
    return out
