"""Процесс payout-worker (ревизия 11, `payout-reconciliation`): outbox `payouts` → pay-emu.

Цикл (опрос, без брокера сообщений):
- `requested` → `attempts+1` (фиксируется ДО отправки) → `POST /v1/refunds` с ключом
  `rr-<refund_request_id>` (тот же ключ на каждом повторе):
  - ответ 200 → `pending|succeeded|failed` по книге эмулятора, `emu_refund_id`;
  - обрыв/тайм-аут/5xx/409 `request_in_progress` → `unknown` (исход неизвестен ≠ выплачено);
  - окончательный отказ эмулятора (400/404/409 amount_exceeds_charge/422) → `failed`;
- `unknown` (не раньше `reconcile_after` с) → сверка `GET /v1/refunds?idempotency_key=`:
  найдено → состояние книги; 404 → `requested` (не принято, повтор с тем же ключом);
- `pending` → `GET /v1/refunds/{emu_refund_id}` до конечного состояния.
Повторы POST ограничены `max_attempts`; после исчерпания бюджета `requested` сверяется
по ключу: процесс мог погибнуть после принятия последнего POST, до записи ответа.
GET-сверка и опрос pending не ограничены бюджетом POST (unknown не превращается в успех).

Переходы — compare-and-set (`apply_result`): из `ALLOWED_FROM[new]`; succeeded/failed конечные
(и в коде, и триггером БД). Запоздалый результат сверки (`pending` после `succeeded`) не меняет
строку.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from darp_range.svckit import TransportError, call, read_ready

from .migrate import open_slice

ALLOWED_FROM = {
    "requested": ("unknown",),
    "unknown": ("requested",),
    "pending": ("requested", "unknown"),
    "succeeded": ("requested", "unknown", "pending"),
    "failed": ("requested", "unknown", "pending"),
}
FINAL_ERRORS = {400, 404, 422}


def apply_result(conn: sqlite3.Connection, payout_id: int, new_state: str, *,
                 emu_refund_id: str | None = None, error: str | None = None) -> bool:
    """Монотонный переход строки выплаты; `False` — результат устарел/недопустим (без записи)."""
    if new_state not in ALLOWED_FROM:
        raise ValueError(f"состояние {new_state!r}")
    allowed = ALLOWED_FROM[new_state]
    marks = ", ".join("?" for _ in allowed)
    try:
        cur = conn.execute(
            f"UPDATE payouts SET state = ?, emu_refund_id = coalesce(?, emu_refund_id), "
            f"last_error = ?, updated_at = ? WHERE id = ? AND state IN ({marks})",
            (new_state, emu_refund_id, error, time.time(), payout_id, *allowed))
    except sqlite3.IntegrityError:
        return False      # триггер конечного состояния
    return cur.rowcount == 1


class Worker:
    def __init__(self, db: str | Path, pay_ready: str | Path, *, log_path: str | Path | None = None,
                 reconcile_after: float = 0.0, max_attempts: int = 8, http_timeout: float = 10.0):
        self.conn = open_slice(db)
        self.pay_ready = Path(pay_ready)
        self.log_path = Path(log_path) if log_path else None
        self.reconcile_after, self.max_attempts = reconcile_after, max_attempts
        self.http_timeout = http_timeout
        self._exhausted: set[int] = set()

    # --- журнал -------------------------------------------------------------------------
    def log(self, **rec: Any) -> None:
        if self.log_path is None:
            return
        rec = {"ts": round(time.time(), 4), "svc": "payout-worker", **rec}
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def _port(self) -> int | None:
        r = read_ready(self.pay_ready)
        return int(r["port"]) if r else None

    def _set(self, row: dict, new: str, **kw: Any) -> bool:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            ok = apply_result(self.conn, row["id"], new, **kw)
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.log(action="transition", payout_id=row["id"], refund_request_id=row["rr"],
                 idempotency_key=row["key"], **{"from": row["state"]}, to=new, applied=ok,
                 error=kw.get("error"))
        return ok

    # --- шаги ---------------------------------------------------------------------------
    def key_for(self, row: dict) -> str:
        return row["key"]

    def send(self, row: dict, port: int) -> None:
        if row["attempts"] >= self.max_attempts:
            if row["id"] not in self._exhausted:
                self._exhausted.add(row["id"])
                self.log(action="attempts_exhausted", payout_id=row["id"],
                         refund_request_id=row["rr"], idempotency_key=row["key"])
            self.reconcile(row, port)
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute("UPDATE payouts SET attempts = attempts + 1, updated_at = ? "
                                    "WHERE id = ? AND state = 'requested'",
                                    (time.time(), row["id"]))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        if cur.rowcount != 1:
            return
        row = dict(row, attempts=row["attempts"] + 1)
        key = self.key_for(row)
        body = {"charge_id": row["charge"], "amount_cents": row["amount"],
                "metadata": {"refund_request_id": row["rr"]}}
        self.log(action="post", payout_id=row["id"], refund_request_id=row["rr"],
                 idempotency_key=key, attempt=row["attempts"])
        try:
            st, resp = call(port, "POST", "/v1/refunds", body,
                            headers={"Idempotency-Key": key}, timeout=self.http_timeout)
        except TransportError as exc:
            self._set(row, "unknown", error=f"transport: {exc}"[:200])
            return
        if st == 200:
            self._set(row, resp["state"], emu_refund_id=resp["id"])
        elif st in FINAL_ERRORS or (st == 409 and (resp or {}).get("error") ==
                                    "amount_exceeds_charge"):
            self._set(row, "failed", error=f"{st} {(resp or {}).get('error')}")
        else:
            self._set(row, "unknown", error=f"{st} {(resp or {}).get('error')}")

    def reconcile(self, row: dict, port: int) -> None:
        if time.time() - row["updated_at"] < self.reconcile_after:
            return
        try:
            st, resp = call(port, "GET", f"/v1/refunds?idempotency_key={row['key']}",
                            timeout=self.http_timeout)
        except TransportError:
            return
        self.log(action="reconcile", payout_id=row["id"], refund_request_id=row["rr"],
                 idempotency_key=row["key"], http_status=st)
        if st == 200 and resp.get("data"):
            r = resp["data"][0]
            self._set(row, r["state"], emu_refund_id=r["id"])
        elif st == 404:
            self._set(row, "requested", error="reconcile: not accepted by emulator")

    def poll(self, row: dict, port: int) -> None:
        if not row["emu"]:
            return
        try:
            st, resp = call(port, "GET", f"/v1/refunds/{row['emu']}", timeout=self.http_timeout)
        except TransportError:
            return
        if st == 200 and resp["state"] != "pending":
            self._set(row, resp["state"], emu_refund_id=resp["id"])

    def tick(self) -> int:
        port = self._port()
        if port is None:
            return 0
        rows = [dict(zip(("id", "rr", "key", "charge", "amount", "state", "emu", "attempts",
                          "updated_at"), r)) for r in self.conn.execute(
            "SELECT id, refund_request_id, idempotency_key, charge_id, amount_cents, state, "
            "emu_refund_id, attempts, updated_at FROM payouts WHERE state IN "
            "('requested', 'unknown', 'pending') ORDER BY id").fetchall()]
        for row in rows:
            {"requested": self.send, "unknown": self.reconcile, "pending": self.poll}[
                row["state"]](row, port)
        return len(rows)


def run(db: str | Path, runtime_dir: str | Path, pay_ready: str | Path, *,
        interval: float = 0.05, **kw: Any) -> None:
    from darp_range.svckit import write_json_atomic
    rt = Path(runtime_dir)
    rt.mkdir(parents=True, exist_ok=True)
    w = Worker(db, pay_ready, log_path=rt / "requests.jsonl", **kw)
    write_json_atomic(rt / "ready.json", {"pid": os.getpid(), "service": "payout-worker",
                                          "options": kw})
    while True:
        try:
            w.tick()
        except sqlite3.OperationalError as exc:      # занятая БД — следующий тик
            w.log(action="db_busy", error=str(exc)[:200])
        time.sleep(interval)
