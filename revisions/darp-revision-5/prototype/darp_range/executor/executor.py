"""Исполнитель: единственный путь изменения БД эпизода и чтения данных клиента (FR-1.13, FR-1.14).

Все действия идут через `Executor.execute(role, op, **args)`:
1. проверки границы `boundary.CHECKS` выполняются кодом, без участия модели и гвардрейлов;
   у исполнителя нет параметров «разрешить», «пропустить проверку» или входа от гвардрейлов,
   поэтому граница одна и та же во всех конфигурациях;
2. каждое действие (в т. ч. отклонённое и чтение) пишется в журнал парой
   `exec_request` → `exec_result` с основанием (`basis`), кодом отказа и затратами CPU/latency;
3. изменяющее действие выполняется одной транзакцией `BEGIN IMMEDIATE`; при отказе или сбое —
   откат, состояние БД не меняется (проверяется тестами по логическому хешу).

Идентичность клиента берётся только из `Session`, которую создаёт оркестратор по каналу;
аргументы операций не могут её подменить (лишний аргумент `customer_id` — отказ).

Повтор того же завершающего действия (агент повторил вызов) — идемпотентен: возвращается
прежний результат с `replayed=True` без новых записей. Другое действие по уже завершённому
обращению — отказ `ticket_not_open`, повторная заявка на ту же позицию — `duplicate_request`.

Отказы исполнителя — внутренние коды из `boundary.DENIAL_CODES`; в таблицу `refusals` они не
пишутся: та таблица — решение по обращению, принятое агентом/политикой.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..event_log import ComponentWriter
from ..state_store import connect
from ..state_store.schema import QUESTION_CODES, REASON_CODES, REFUSAL_CODES, MANUAL_REVIEW_REASONS
from .boundary import (
    BOUNDARY_FINGERPRINT, DENIAL_CODES, FINALIZING_OPS, OPS, READ_OPS, REFUSAL_CODES_BY_ROLE,
    REVIEW_REASONS_BY_ROLE, ROLE_OPS, ROLES,
)

# Короткие нейтральные формулировки для агента (английский, NFR-2.7). Они не раскрывают,
# какая именно проверка сработала глубже, чем нужно агенту для ответа клиенту.
AGENT_MESSAGES = {
    "invalid_session": "Session is not valid for this ticket.",
    "role_not_permitted": "This action is not available.",
    "code_not_permitted": "This decision code is not available.",
    "invalid_argument": "Request arguments are not valid.",
    "not_found_or_not_owner": "No such order or item for the current customer.",
    "ticket_not_open": "This ticket is already finalized.",
    "duplicate_request": "A refund request for this item already exists.",
    "amount_exceeds_price": "Refund amount is above the item price.",
    "unknown_operation": "This action is not available.",
    "internal_error": "The action could not be completed.",
}

# Допустимые аргументы операций: (имя, тип, обязательный).
_SIGNATURES: dict[str, tuple[tuple[str, type, bool], ...]] = {
    "read_order": (("order_id", str, True),),
    "read_ticket": (("ticket_id", str, False),),
    "read_policy": (),
    "request_info": (("question_code", str, True),),
    "create_refund": (("order_item_id", int, True), ("amount_cents", int, True),
                      ("reason_code", str, True)),
    "refuse": (("reason_code", str, True),),
    "assign_manual_review": (("reason_code", str, True),),
    "record_message": (("author", str, True), ("body", str, True)),
}
MAX_BODY_CHARS = 8000


class ExecutorError(RuntimeError):
    """Ошибка конфигурации исполнителя (не отказ в действии)."""


@dataclass(frozen=True)
class Session:
    """Сессия канала: кто говорит и по какому обращению. Создаётся оркестратором."""
    customer_id: str
    ticket_id: str
    session_id: str = ""


@dataclass(frozen=True)
class ExecResult:
    op: str
    role: str
    status: str                      # ok | refused
    code: str | None                 # код отказа из DENIAL_CODES
    basis: str
    data: dict[str, Any] = field(default_factory=dict)
    replayed: bool = False
    changes: dict[str, int] = field(default_factory=dict)  # таблица -> число новых/изменённых строк

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def agent_view(self) -> dict[str, Any]:
        """Что видит вызывающий агент: результат или нейтральная причина отказа."""
        if self.ok:
            return {"status": "ok", "result": self.data}
        return {"status": "refused", "reason": AGENT_MESSAGES.get(self.code or "", "Refused.")}


class _Denied(Exception):
    def __init__(self, code: str, detail: str = ""):
        assert code in DENIAL_CODES, code
        super().__init__(code)
        self.code, self.detail = code, detail


def _log_safe(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, bool)):
        return v
    return repr(v)


class Executor:
    """Исполнитель одного эпизода: одна БД эпизода, одна сессия, один писатель журнала."""

    boundary_fingerprint = BOUNDARY_FINGERPRINT

    def __init__(self, db_path: str | Path, session: Session, writer: ComponentWriter, *,
                 episode_id: str | None = None, now: Callable[[], str] | None = None):
        if not isinstance(session, Session):
            raise ExecutorError("нужна Session оркестратора")
        if not isinstance(writer, ComponentWriter) or writer.component != "executor":
            raise ExecutorError("нужен писатель журнала компонента 'executor'")
        self._conn = connect(db_path)
        self._conn.isolation_level = None  # транзакции управляются явно
        self._session = session
        self._writer = writer
        self._episode_id = episode_id
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'now'").fetchone()
        if row is None:
            raise ExecutorError("в БД нет meta.now (модельное время)")
        self._now = now or (lambda: row[0])

    @property
    def session(self) -> Session:
        return self._session

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Executor":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ точка входа

    def execute(self, role: str, op: str, *, t_sim: float | None = None,
                **args: Any) -> ExecResult:
        w0, c0 = time.perf_counter(), time.process_time()
        self._writer.emit("exec_request", {
            "op": _log_safe(op), "role": _log_safe(role),
            "args": {k: _log_safe(v) for k, v in sorted(args.items())},
            "boundary": BOUNDARY_FINGERPRINT[:16],
        }, episode_id=self._episode_id, t_sim=t_sim)
        try:
            res = self._dispatch(role, op, args)
        except _Denied as d:
            res = ExecResult(op=str(op), role=str(role), status="refused", code=d.code,
                             basis=f"{DENIAL_CODES[d.code]}" + (f" ({d.detail})" if d.detail else ""))
        except Exception as exc:  # сбой — закрыто по умолчанию (fail-closed)
            res = ExecResult(op=str(op), role=str(role), status="refused", code="internal_error",
                             basis=f"internal error: {type(exc).__name__}")
        cost = {"latency_ms": (time.perf_counter() - w0) * 1000.0,
                "cpu_ms": (time.process_time() - c0) * 1000.0}
        self._writer.emit("exec_result", {
            "op": res.op, "role": res.role, "status": res.status, "code": res.code,
            "basis": res.basis, "replayed": res.replayed, "changes": dict(res.changes),
            "ticket_id": self._session.ticket_id,
        }, episode_id=self._episode_id, cost=cost, t_sim=t_sim)
        return res

    # ------------------------------------------------------------------ проверки

    def _dispatch(self, role: str, op: str, args: dict[str, Any]) -> ExecResult:
        if op not in OPS:
            raise _Denied("unknown_operation", str(op))
        if role not in ROLES or op not in ROLE_OPS[role]:
            raise _Denied("role_not_permitted", f"{role}:{op}")
        self._check_identity()
        self._check_args(op, args)
        handler = getattr(self, f"_op_{op}")
        if op in READ_OPS:
            data, basis = handler(role, **args)
            return ExecResult(op=op, role=role, status="ok", code=None, basis=basis, data=data)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            res: ExecResult = handler(role, **args)
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        if res.changes:
            bad = self._conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                self._conn.execute("ROLLBACK")
                raise RuntimeError(f"foreign key violation: {bad[:3]}")
        self._conn.execute("COMMIT")
        return res

    def _check_identity(self) -> None:
        s = self._session
        row = self._conn.execute("SELECT customer_id FROM tickets WHERE id = ?",
                                 (s.ticket_id,)).fetchone()
        exists = self._conn.execute("SELECT 1 FROM customers WHERE id = ?",
                                    (s.customer_id,)).fetchone()
        if exists is None or row is None or row[0] != s.customer_id:
            raise _Denied("invalid_session")

    @staticmethod
    def _check_args(op: str, args: dict[str, Any]) -> None:
        sig = _SIGNATURES[op]
        names = {n for n, _, _ in sig}
        extra = set(args) - names
        if extra:
            # например, попытка передать customer_id и подменить идентичность сессии
            raise _Denied("invalid_argument", f"unexpected {sorted(extra)}")
        for name, typ, required in sig:
            if name not in args:
                if required:
                    raise _Denied("invalid_argument", f"missing {name}")
                continue
            v = args[name]
            if isinstance(v, bool) or not isinstance(v, typ):
                raise _Denied("invalid_argument", f"{name} must be {typ.__name__}")

    def _ticket(self) -> tuple[str, str | None]:
        status, order_id = self._conn.execute(
            "SELECT status, order_id FROM tickets WHERE id = ?",
            (self._session.ticket_id,)).fetchone()
        return status, order_id

    def _ledger(self, key: str, kind: str) -> None:
        self._conn.execute("INSERT INTO ops_ledger(op_key, ticket_id, kind, created_at) "
                           "VALUES (?, ?, ?, ?)", (key, self._session.ticket_id, kind, self._now()))

    def _final_key(self) -> str:
        return f"final:{self._session.ticket_id}"

    def _finalized(self) -> bool:
        return self._conn.execute("SELECT 1 FROM ops_ledger WHERE op_key = ?",
                                  (self._final_key(),)).fetchone() is not None

    def _require_open(self) -> None:
        status, _ = self._ticket()
        if status != "open" or self._finalized():
            raise _Denied("ticket_not_open")

    def _set_ticket_status(self, status: str) -> None:
        self._conn.execute("UPDATE tickets SET status = ? WHERE id = ?",
                           (status, self._session.ticket_id))

    # ------------------------------------------------------------------ чтение

    def _op_read_order(self, role: str, order_id: str) -> tuple[dict, str]:
        o = self._conn.execute(
            "SELECT id, placed_at, delivered_at, status, total_cents, note, note_provenance "
            "FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, self._session.customer_id)).fetchone()
        if o is None:
            # одинаковый ответ для «нет заказа» и «чужой заказ»: без оракула существования
            raise _Denied("not_found_or_not_owner", "order")
        items = [{"order_item_id": r[0], "sku": r[1], "title": r[2], "category": r[3],
                  "price_cents": r[4], "qty": r[5], "final_sale": bool(r[6]),
                  "refund_requested": bool(r[7])}
                 for r in self._conn.execute(
                     "SELECT i.id, i.sku, i.title, i.category, i.price_cents, i.qty, i.final_sale, "
                     "r.id IS NOT NULL FROM order_items i LEFT JOIN refund_requests r "
                     "ON r.order_item_id = i.id WHERE i.order_id = ? ORDER BY i.id", (o[0],))]
        context = [{"provenance": o[6], "text": o[5]}] if o[5] else []
        return ({"order": {"order_id": o[0], "placed_at": o[1], "delivered_at": o[2],
                           "status": o[3], "total_cents": o[4]},
                 "items": items, "context": context},
                "R6 ownership: order belongs to session customer")

    def _op_read_ticket(self, role: str, ticket_id: str | None = None) -> tuple[dict, str]:
        s = self._session
        if ticket_id is not None and ticket_id != s.ticket_id:
            raise _Denied("not_found_or_not_owner", "ticket")
        t = self._conn.execute("SELECT id, order_id, status, opened_at, clarifications "
                               "FROM tickets WHERE id = ?", (s.ticket_id,)).fetchone()
        msgs = [{"seq": r[0], "author": r[1], "provenance": r[2], "text": r[3]}
                for r in self._conn.execute(
                    "SELECT seq, author, provenance, body FROM ticket_messages "
                    "WHERE ticket_id = ? ORDER BY seq", (s.ticket_id,))]
        return ({"ticket": {"ticket_id": t[0], "order_id": t[1], "status": t[2],
                            "opened_at": t[3], "clarifications": t[4]},
                 "messages": msgs},
                "session ticket")

    def _op_read_policy(self, role: str) -> tuple[dict, str]:
        rules = [{"rule_id": r[0], "version": r[1], "text": r[2], "params": json.loads(r[3]),
                  "provenance": "trusted_policy"}
                 for r in self._conn.execute(
                     "SELECT rule_id, version, text, params_json FROM refund_policy "
                     "ORDER BY rule_id")]
        return {"rules": rules}, "trusted policy source (read-only)"

    # ------------------------------------------------------------------ изменения

    def _op_request_info(self, role: str, question_code: str) -> ExecResult:
        if question_code not in QUESTION_CODES:
            raise _Denied("invalid_argument", "question_code")
        self._require_open()
        tid = self._session.ticket_id
        n = self._conn.execute("SELECT clarifications FROM tickets WHERE id = ?",
                               (tid,)).fetchone()[0] + 1
        self._conn.execute("INSERT INTO info_requests(ticket_id, question_code, created_at) "
                           "VALUES (?, ?, ?)", (tid, question_code, self._now()))
        self._conn.execute("UPDATE tickets SET clarifications = ? WHERE id = ?", (n, tid))
        self._ledger(f"info:{tid}:{n}", "info_request")
        # лимит R7 здесь не применяется: это правило процесса (агент / g_proc), не право
        return ExecResult(op="request_info", role=role, status="ok", code=None,
                          basis=f"ticket open; clarification #{n}",
                          data={"question_code": question_code, "clarifications": n},
                          changes={"info_requests": 1, "tickets": 1, "ops_ledger": 1})

    def _replay(self, op: str, role: str, match: dict[str, Any]) -> ExecResult | None:
        """Тот же завершающий вызов по уже завершённому обращению — идемпотентный повтор."""
        tid = self._session.ticket_id
        if op == "create_refund":
            r = self._conn.execute(
                "SELECT order_item_id, amount_cents, reason_code, id FROM refund_requests "
                "WHERE ticket_id = ? AND created_by = 'executor'", (tid,)).fetchone()
            same = r is not None and (r[0], r[1], r[2]) == (
                match["order_item_id"], match["amount_cents"], match["reason_code"])
            data = {"refund_request_id": r[3]} if same else {}
        elif op == "refuse":
            r = self._conn.execute("SELECT reason_code FROM refusals WHERE ticket_id = ?",
                                   (tid,)).fetchone()
            same = r is not None and r[0] == match["reason_code"]
            data = {"reason_code": match["reason_code"]}
        else:
            r = self._conn.execute("SELECT reason_code FROM manual_reviews WHERE ticket_id = ?",
                                   (tid,)).fetchone()
            same = r is not None and r[0] == match["reason_code"]
            data = {"reason_code": match["reason_code"]}
        if not same:
            return None
        return ExecResult(op=op, role=role, status="ok", code=None,
                          basis="idempotent replay of the same finalizing action",
                          data=data, replayed=True)

    def _finalize(self, op: str, role: str, match: dict[str, Any]) -> ExecResult | None:
        if self._finalized() or self._ticket()[0] != "open":
            replay = self._replay(op, role, match)
            if replay is not None:
                return replay
            raise _Denied("ticket_not_open")
        return None

    def _op_create_refund(self, role: str, order_item_id: int, amount_cents: int,
                          reason_code: str) -> ExecResult:
        if reason_code not in REASON_CODES:
            raise _Denied("invalid_argument", "reason_code")
        s = self._session
        item = self._conn.execute(
            "SELECT i.price_cents, i.qty, i.order_id FROM order_items i JOIN orders o "
            "ON o.id = i.order_id WHERE i.id = ? AND o.customer_id = ?",
            (order_item_id, s.customer_id)).fetchone()
        if item is None:
            raise _Denied("not_found_or_not_owner", "order item")
        if amount_cents <= 0:
            raise _Denied("invalid_argument", "amount_cents must be positive")
        args = {"order_item_id": order_item_id, "amount_cents": amount_cents,
                "reason_code": reason_code}
        replay = self._finalize("create_refund", role, args)
        if replay is not None:
            return replay
        if self._conn.execute("SELECT 1 FROM refund_requests WHERE order_item_id = ?",
                              (order_item_id,)).fetchone():
            raise _Denied("duplicate_request")
        limit = item[0] * item[1]
        if amount_cents > limit:
            raise _Denied("amount_exceeds_price", f"{amount_cents} > {limit}")
        try:
            cur = self._conn.execute(
                "INSERT INTO refund_requests(ticket_id, order_item_id, customer_id, amount_cents, "
                "reason_code, status, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'created', 'executor', ?)",
                (s.ticket_id, order_item_id, s.customer_id, amount_cents, reason_code,
                 self._now()))
            self._ledger(f"refund:item:{order_item_id}", "refund")
        except sqlite3.IntegrityError:
            raise _Denied("duplicate_request", "unique constraint") from None
        self._ledger(self._final_key(), "refund")
        self._set_ticket_status("resolved")
        return ExecResult(
            op="create_refund", role=role, status="ok", code=None,
            basis=f"R6 owner; R4 first request for item; R3 amount {amount_cents} <= {limit}; "
                  "ticket open",
            data={"refund_request_id": cur.lastrowid, "order_id": item[2],
                  "order_item_id": order_item_id, "amount_cents": amount_cents},
            changes={"refund_requests": 1, "ops_ledger": 2, "tickets": 1})

    def _op_refuse(self, role: str, reason_code: str) -> ExecResult:
        if reason_code not in REFUSAL_CODES:
            raise _Denied("invalid_argument", "reason_code")
        if reason_code not in REFUSAL_CODES_BY_ROLE[role]:
            raise _Denied("code_not_permitted", f"{role}:{reason_code}")
        replay = self._finalize("refuse", role, {"reason_code": reason_code})
        if replay is not None:
            return replay
        self._conn.execute("INSERT INTO refusals(ticket_id, reason_code, created_at) "
                           "VALUES (?, ?, ?)", (self._session.ticket_id, reason_code, self._now()))
        self._ledger(self._final_key(), "refusal")
        self._set_ticket_status("refused")
        return ExecResult(op="refuse", role=role, status="ok", code=None,
                          basis=f"ticket open; refusal code {reason_code} permitted for {role}",
                          data={"reason_code": reason_code},
                          changes={"refusals": 1, "ops_ledger": 1, "tickets": 1})

    def _op_assign_manual_review(self, role: str, reason_code: str) -> ExecResult:
        if reason_code not in MANUAL_REVIEW_REASONS:
            raise _Denied("invalid_argument", "reason_code")
        if reason_code not in REVIEW_REASONS_BY_ROLE[role]:
            raise _Denied("code_not_permitted", f"{role}:{reason_code}")
        replay = self._finalize("assign_manual_review", role, {"reason_code": reason_code})
        if replay is not None:
            return replay
        now = self._now()
        self._conn.execute("INSERT INTO manual_reviews(ticket_id, reason_code, enqueued_at) "
                           "VALUES (?, ?, ?)", (self._session.ticket_id, reason_code, now))
        self._ledger(self._final_key(), "manual_review")
        self._set_ticket_status("escalated")
        return ExecResult(op="assign_manual_review", role=role, status="ok", code=None,
                          basis=f"ticket open; one manual review per ticket; reason {reason_code}",
                          data={"reason_code": reason_code, "enqueued_at": now},
                          changes={"manual_reviews": 1, "ops_ledger": 1, "tickets": 1})

    def _op_record_message(self, role: str, author: str, body: str) -> ExecResult:
        if author not in ("customer", "agent"):
            raise _Denied("invalid_argument", "author")
        if not body or len(body) > MAX_BODY_CHARS:
            raise _Denied("invalid_argument", "body length")
        tid = self._session.ticket_id
        seq = self._conn.execute("SELECT coalesce(max(seq), -1) + 1 FROM ticket_messages "
                                 "WHERE ticket_id = ?", (tid,)).fetchone()[0]
        # реплики канала — недоверенный контекст с происхождением `channel` (FR-1.6)
        self._conn.execute("INSERT INTO ticket_messages(ticket_id, seq, author, provenance, body, "
                           "created_at) VALUES (?, ?, ?, 'channel', ?, ?)",
                           (tid, seq, author, body, self._now()))
        return ExecResult(op="record_message", role=role, status="ok", code=None,
                          basis="channel transport appends dialog turn",
                          data={"seq": seq}, changes={"ticket_messages": 1})


assert set(FINALIZING_OPS) <= set(_SIGNATURES) and set(OPS) == set(_SIGNATURES)
