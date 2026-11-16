"""In-process адаптер `TrafficPort` — текущая внутрянка трафика за контрактом traffic-svc
(issue #19; сервис — #36).

- расписание: `plan.build_schedule` (блоки `manual_queue.build_flow` из seed конфигурации);
- привязка обращения к реальным строкам восстановленного снимка: `corpus.pick_accounts` +
  `corpus.bind` (аккаунт, заказ, позиции, тикет), доверенные записи эпизода по режиму плана;
- клиент: `ResponsiveUser` с репликами и фактами клиента (ревизия 1: отвечает на уточнения
  агента по смыслу вопроса; ревизия 6: режим ответа на просьбу подтвердить — из плана).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from darp_range.channel import ResponsiveUser
from darp_range.corpus import bind, pick_accounts
from darp_range.state_store import connect

from .flow import trusted_fixture, trusted_fixture_gt_rev6
from .plan import build_schedule
from .ports import BoundEpisode, FlowInvite


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
