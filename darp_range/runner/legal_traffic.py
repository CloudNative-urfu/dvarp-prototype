"""In-process адаптер `TrafficPort` — текущая внутрянка трафика за контрактом traffic-svc
(issue #19; сервис — #36).

- расписание: `plan.build_schedule` (блоки `manual_queue.build_flow` из seed конфигурации);
- привязка обращения к реальным строкам восстановленного снимка: `corpus.pick_accounts` +
  `corpus.bind` (аккаунт, заказ, позиции, тикет), доверенные записи эпизода по режиму плана;
- клиент: `ResponsiveUser` с репликами и фактами клиента (ревизия 1: отвечает на уточнения
  агента по смыслу вопроса; ревизия 6: режим ответа на просьбу подтвердить — из плана).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from darp_range.channel import ResponsiveUser
from darp_range.corpus import bind, pick_accounts
from darp_range.manual_queue import attack_count, build_flow
from darp_range.repro import check_seed, derive_seed, make_rng
from darp_range.state_store import connect

from .flow import trusted_fixture, trusted_fixture_gt_rev6
from .plan import BlockSchedule, FlowSlot, RunnerError, RunPlan, Schedule, select_entries
from .ports import BoundEpisode, FlowInvite


def build_schedule(seed: int, plan: RunPlan, entries: Sequence[Mapping[str, Any]]) -> Schedule:
    check_seed(seed)
    sel = select_entries(entries, plan)
    order = {e["id"]: i for i, e in enumerate(entries)}
    controls = [e["id"] for e in sel if e["kind"] == "control"]
    attacks = [e["id"] for e in sel if e["kind"] == "attack"]
    n_legal = plan.n_legal or len(controls)
    per_block = attack_count(n_legal, plan.w_a) if attacks else 0
    if attacks and per_block == 0:
        raise RunnerError(f"при n_legal={n_legal} и w_A={plan.w_a} в блоке нет ни одной атаки")
    blocks = plan.blocks or (math.ceil(len(attacks) / per_block) if attacks else 1)
    att_perm = list(attacks)
    make_rng(seed, "runner", "attack-order").shuffle(att_perm)
    out: list[BlockSchedule] = []
    for b in range(blocks):
        fseed = derive_seed(seed, "runner", "flow", b)
        ctl_perm = list(controls)
        make_rng(seed, "runner", "legal-order", b).shuffle(ctl_perm)
        raw = build_flow(fseed, n_legal, plan.w_a, plan.mean_gap, with_attacks=bool(attacks))
        slots = []
        for s in raw:
            if s.traffic == "legal":
                eid = ctl_perm[s.index % len(ctl_perm)]
            else:
                eid = att_perm[(b * per_block + s.index) % len(att_perm)]
            slots.append(FlowSlot(s.case_id, s.traffic, s.t_arrival, s.index, eid, order[eid]))
        out.append(BlockSchedule(b, fseed, tuple(slots)))
    return Schedule(seed, tuple(e["id"] for e in sel), tuple(controls), tuple(attacks), n_legal,
                    per_block, tuple(out))


class CorpusTraffic:
    def __init__(self, ctx: Any):
        self.plan, self.mgr = ctx.plan, ctx.mgr
        self.entries = ctx.entries

    def schedule(self, invite: FlowInvite):
        if invite.snapshot_hash != self.mgr.baseline_hash:
            raise ValueError(f"{invite.snapshot_id}: хеш снимка в приглашении не совпадает")
        return build_schedule(invite.seed, self.plan, list(self.entries.values())).blocks

    def bind(
        self,
        entry: Mapping[str, Any],
        entry_index: int,
        db_path: Path,
        *,
        episode_id: str,
        traffic: str,
    ) -> BoundEpisode:
        accounts = self.mgr.spec.accounts
        actor, victim = pick_accounts(accounts, entry["kind"], entry_index)
        conn = connect(db_path)
        try:
            b = bind(
                entry, conn, accounts, actor_id=actor, victim_id=victim, ticket_id=f"T-{episode_id}"
            )
            fx = b.defender_fixture()
            if self.plan.trusted_mode == "gt_rev6":
                # исторический режим rev6 (только воспроизведение прогонов boundary-2 rev6)
                fx = dict(fx, trusted=trusted_fixture_gt_rev6(conn, b, traffic))
            elif self.plan.trusted_mode == "declared":
                fx = dict(fx, trusted=trusted_fixture(conn, b))
        finally:
            conn.close()
        return BoundEpisode(
            actor_id=actor,
            ticket_id=b.ticket_id,
            order_id=b.fixture["tickets"][0].get("order_id"),
            fixture=fx,
            truth=None,
            handle=b,
        )

    def client(self, ep: BoundEpisode) -> ResponsiveUser:
        b = ep.handle
        return ResponsiveUser(b.turns, b.customer_facts, confirm_mode=self.plan.legal_confirm)

    def close(self) -> dict[str, Any]:
        return {}
