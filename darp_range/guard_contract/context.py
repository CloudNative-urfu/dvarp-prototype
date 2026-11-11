"""Сборка фрагментов контекста с происхождением из результатов чтения исполнителя (FR-1.6).

Исполнитель уже возвращает происхождение (`provenance`) для каждого текста, который агент
может прочитать: примечание к заказу (`order_data`), реплики обращения (`channel`,
`ticket_history`, `system`), правила (`trusted_policy`). Здесь эти результаты переводятся
в `ContextFragment` со ссылкой `source` на место в данных — без потери происхождения.
"""

from __future__ import annotations

from typing import Any, Mapping

from .types import ContextFragment, GuardContractError


def fragments_from_read(op: str, result: Mapping[str, Any]) -> tuple[ContextFragment, ...]:
    """`op` — операция чтения исполнителя (`read_order`/`read_ticket`/`read_policy`),
    `result` — `ExecResult.data`."""
    if op == "read_order":
        oid = result["order"]["order_id"]
        return tuple(ContextFragment(c["text"], c["provenance"], f"order:{oid}:note:{i}")
                     for i, c in enumerate(result.get("context", ())))
    if op == "read_ticket":
        tid = result["ticket"]["ticket_id"]
        return tuple(ContextFragment(m["text"], m["provenance"], f"ticket:{tid}:msg:{m['seq']}")
                     for m in result.get("messages", ()))
    if op == "read_policy":
        return tuple(ContextFragment(r["text"], r["provenance"], f"policy:{r['rule_id']}:v{r['version']}")
                     for r in result.get("rules", ()))
    raise GuardContractError(f"операция {op!r} не возвращает контекст")
