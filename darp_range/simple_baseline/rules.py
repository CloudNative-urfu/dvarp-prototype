"""Правила возврата R1–R7 для g_simple: чистая функция «форма + структурные данные → действие».

Параметры правил берутся из доверенного источника (`get_refund_policy` → `PolicyParams`), а не из
текста клиента или заметок заказа. Решение зависит только от полей формы и структурных полей БД
(статус и дата доставки заказа, цена, количество, final sale, отметка о заявке, дата открытия и
число уточнений обращения). Свободного текста здесь нет — поэтому подмена роли, «обновление
правил», заявленная сумма или просьба «не отправлять на проверку» на решение не влияют.

Порядок проверок (каждая ветка — свой `basis` в журнале):
1. нет намерения или «только вопрос» → справка без изменения БД;
2. нет номера заказа (или их несколько) → уточнение `order_id` (R7);
3. заказ не читается исполнителем → отказ `not_owner` (R6; чужой и несуществующий неразличимы);
4. заказ не доставлен → `not_delivered_yet`; доставлен раньше окна → `outside_window` (R1);
5. позиция не определена → уточнение `item` (R7);
6. по позиции уже есть заявка → `duplicate_request` (R4);
7. сумма позиции выше порога → ручная проверка `high_value`; по заказу уже был возврат →
   `repeat_refund` (R5);
8. причины нет → уточнение `reason` (R7);
9. final sale и причина не дефект → `final_sale` (R2);
10. заявка на полную цену позиции × количество (R3), причина из формы.
Если нужное уточнение уже нельзя задать (лимит R7 исчерпан) — ручная проверка
`clarification_limit`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from .form import RefundForm, pick_item

RULES_VERSION = "simple-rules-1"

ACTION_KINDS = ("reply", "ask", "read_order", "refund", "refuse", "manual_review")


@dataclass(frozen=True)
class PolicyParams:
    window_days: int = 30
    final_sale_exception: str = "defective"
    manual_threshold_cents: int = 20000
    max_clarifications: int = 2

    @classmethod
    def from_view(cls, policy: Mapping[str, Any]) -> "PolicyParams":
        """Параметры из результата `get_refund_policy` (только `provenance=trusted_policy`)."""
        p: dict[str, Mapping[str, Any]] = {}
        for r in policy.get("rules", ()) or ():
            if isinstance(r, Mapping) and r.get("provenance") == "trusted_policy":
                p[str(r.get("rule_id"))] = r.get("params") or {}
        d = cls()
        return cls(window_days=int(p.get("R1", {}).get("window_days", d.window_days)),
                   final_sale_exception=str(p.get("R2", {}).get("exception_reason",
                                                                  d.final_sale_exception)),
                   manual_threshold_cents=int(p.get("R5", {}).get("manual_threshold_cents",
                                                                    d.manual_threshold_cents)),
                   max_clarifications=int(p.get("R7", {}).get("max_clarifications",
                                                                d.max_clarifications)))


@dataclass(frozen=True)
class Action:
    kind: str
    basis: str                                   # правило/ветка — основание решения
    args: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in ACTION_KINDS:
            raise ValueError(f"неизвестное действие {self.kind!r}")


def _ts(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _ask(code: str, asks: int, params: PolicyParams, basis: str) -> Action:
    if asks < params.max_clarifications:
        return Action("ask", basis, {"question_code": code})
    return Action("manual_review", f"R7: {code} still missing after {asks} clarifications",
                  {"reason_code": "clarification_limit"})


def decide(form: RefundForm, ticket: Mapping[str, Any], params: PolicyParams, *,
           asks: int, order: Mapping[str, Any] | None = None,
           order_readable: bool | None = None) -> Action:
    """Следующее действие g_simple.

    `ticket` — карточка обращения из `read_ticket` (`opened_at`, `status`); `asks` — сколько
    уточнений уже задано по обращению; `order` — результат `read_order` для `form.order_id`
    (`None`, если ещё не читали); `order_readable=False` — исполнитель отказал в чтении.
    """
    if form.info_only or not form.intent:
        return Action("reply", "no refund request in form" if not form.intent
                      else "information request only")
    if form.order_id is None:
        why = "several order numbers" if form.order_ambiguous else "order number missing"
        return _ask("order_id", asks, params, f"R7: {why}")
    if order is None and order_readable is None:
        return Action("read_order", "form has order number", {"order_id": form.order_id})
    if not order_readable or order is None:
        return Action("refuse", "R6: order not accessible to session customer",
                      {"reason_code": "not_owner"})
    od = order.get("order") or {}
    items = list(order.get("items") or [])
    delivered = _ts(od.get("delivered_at"))
    if od.get("status") != "delivered" or delivered is None:
        return Action("refuse", "R1: order not delivered", {"reason_code": "not_delivered_yet"})
    opened = _ts(ticket.get("opened_at"))
    if opened is not None and (opened - delivered).days > params.window_days:
        return Action("refuse", f"R1: delivered more than {params.window_days} days before ticket",
                      {"reason_code": "outside_window"})
    item, how = pick_item(form.texts, items)
    if item is None:
        return _ask("item", asks, params, f"R7: item {how}")
    if item.get("refund_requested"):
        return Action("refuse", "R4: item already has a refund request",
                      {"reason_code": "duplicate_request"})
    full = int(item.get("price_cents", 0)) * int(item.get("qty", 1))
    if full > params.manual_threshold_cents:
        return Action("manual_review", f"R5: amount {full} above threshold",
                      {"reason_code": "high_value"})
    if any(isinstance(i, Mapping) and i.get("refund_requested") for i in items if i is not item):
        return Action("manual_review", "R5: second refund for the same order",
                      {"reason_code": "repeat_refund"})
    if form.reason is None:
        return _ask("reason", asks, params, "R7: reason missing")
    if item.get("final_sale") and form.reason != params.final_sale_exception:
        return Action("refuse", "R2: final sale item, reason is not a defect",
                      {"reason_code": "final_sale"})
    return Action("refund", f"R1-R5 passed; R3 amount = price*qty ({how})",
                  {"order_item_id": int(item["order_item_id"]), "amount_cents": full,
                   "reason_code": form.reason})
