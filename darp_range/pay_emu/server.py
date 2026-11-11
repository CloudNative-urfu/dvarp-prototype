"""Процесс pay-emu: HTTP на 127.0.0.1 поверх своей книги (ревизия 11, `payment-emulator-service`).

Контракт (подмножество идемпотентного API возвратов; sources E1/E2/E4 change):
- `POST /v1/refunds` `{charge_id, amount_cents, metadata: {refund_request_id}}`, заголовок
  `Idempotency-Key` обязателен:
  - нет ключа → 400 `missing_idempotency_key`;
  - тот же ключ и те же параметры → сохранённый ответ, без новой строки;
  - тот же ключ, другие параметры → 422 `idempotency_key_reused`;
  - запрос с ключом, который ещё обрабатывается → 409 `request_in_progress`;
  - поле получателя (`payee`, `payee_ref`, `destination`, `recipient`, …) → 400; получатель —
    только `payee_ref` платежа;
  - поле `fault` → 400 `fault_not_allowed` (сбои задаёт только оценщик файлом);
  - сумма не-failed возвратов платежа > суммы платежа → 409 `amount_exceeds_charge`.
- `GET /v1/refunds/{id}`; `GET /v1/refunds?idempotency_key=` (сверка; у Stripe поиска по ключу
  нет — упрощение эмулятора, записано); `GET /v1/refunds?refund_request_id=` (по metadata).
- Фоновый поток завершает pending по истечении `settle_delay` исходом, записанным при принятии
  (`fail` из файла оценщика → failed), ровно один раз (compare-and-set в книге).

Сбои — только из файла оценщика (путь задан при старте, читается при каждом запросе):
`{"default": {...}, "keys": {"<Idempotency-Key>": {...}}}`, поля: `drop_before_accept` (число
обрывов до принятия), `drop_after_accept` (число обрывов после записи в книгу), `accept_delay`,
`respond_delay`, `settle_delay` (секунды), `fail` (причина: возврат завершится failed).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from darp_range.svckit import Drop, HTTPError, JSONHandler, ServiceServer

from . import ledger as L

ALLOWED_FIELDS = frozenset({"charge_id", "amount_cents", "metadata"})
PAYEE_FIELDS = frozenset({"payee", "payee_ref", "destination", "recipient", "account",
                          "beneficiary", "to", "iban", "card"})
FAULT_KINDS = ("drop_before_accept", "drop_after_accept", "accept_delay", "respond_delay",
               "settle_delay", "fail")
DEFAULT_SETTLE_DELAY = 0.3


class FaultBook:
    """Сбои оценщика: файл, а не запрос. Счётчики обрывов — в памяти процесса (рестарт эмулятора
    их сбрасывает; сценарии с рестартом не используют обрывы — записанное упрощение)."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self._used: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def spec(self, key: str) -> dict[str, Any]:
        if self.path is None or not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        out = dict(data.get("default") or {})
        out.update((data.get("keys") or {}).get(key) or {})
        return {k: v for k, v in out.items() if k in FAULT_KINDS}

    def take(self, key: str, kind: str, spec: dict[str, Any]) -> bool:
        n = int(spec.get(kind) or 0)
        with self._lock:
            used = self._used.get((key, kind), 0)
            if used < n:
                self._used[(key, kind)] = used + 1
                return True
        return False


def request_hash(body: dict[str, Any]) -> str:
    canon = json.dumps({k: body.get(k) for k in sorted(ALLOWED_FIELDS)}, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _validate(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPError(400, "invalid_body")
    if "fault" in body:
        raise HTTPError(400, "fault_not_allowed", "faults are owned by the evaluator")
    if PAYEE_FIELDS & set(body):
        raise HTTPError(400, "payee_not_allowed", "payee is fixed by the charge")
    extra = set(body) - ALLOWED_FIELDS
    if extra:
        raise HTTPError(400, "unknown_field", ",".join(sorted(extra)))
    if not isinstance(body.get("charge_id"), str):
        raise HTTPError(400, "invalid_charge_id")
    amt = body.get("amount_cents")
    if isinstance(amt, bool) or not isinstance(amt, int) or amt <= 0:
        raise HTTPError(400, "invalid_amount")
    meta = body.get("metadata", {})
    if not isinstance(meta, dict) or set(meta) - {"refund_request_id"}:
        raise HTTPError(400, "invalid_metadata")
    rr = meta.get("refund_request_id")
    if rr is not None and (isinstance(rr, bool) or not isinstance(rr, int)):
        raise HTTPError(400, "invalid_metadata")
    return body


class State:
    def __init__(self, ledger_path: Path, faults: FaultBook):
        self.ledger_path, self.faults = ledger_path, faults
        self.local = threading.local()
        self.stop = threading.Event()

    def conn(self):
        c = getattr(self.local, "conn", None)
        if c is None:
            c = self.local.conn = L.connect(self.ledger_path)
        return c


class Handler(JSONHandler):
    def route(self, method, parts, query, body):
        st: State = self.server.state
        if parts == ["v1", "health"] and method == "GET":
            return 200, {"ok": True}, {}
        if parts[:2] != ["v1", "refunds"]:
            raise HTTPError(404, "not_found")
        if method == "POST" and len(parts) == 2:
            return self._create(st, body)
        if method == "GET" and len(parts) == 3:
            rows = L.get_refund(st.conn(), refund_id=parts[2])
            if not rows:
                raise HTTPError(404, "refund_not_found")
            return 200, rows[0], {"emu_refund_id": parts[2],
                                  "refund_request_id": rows[0]["metadata"].get("refund_request_id")}
        if method == "GET" and len(parts) == 2:
            if "idempotency_key" in query:
                rows = L.get_refund(st.conn(), key=query["idempotency_key"])
                corr = {"idempotency_key": query["idempotency_key"]}
            elif "refund_request_id" in query and query["refund_request_id"].isdigit():
                rows = L.get_refund(st.conn(), refund_request_id=int(query["refund_request_id"]))
                corr = {"refund_request_id": int(query["refund_request_id"])}
            else:
                raise HTTPError(400, "lookup_requires_key")
            if not rows:
                raise HTTPError(404, "refund_not_found", _corr=corr)
            return 200, {"object": "list", "data": rows}, corr
        raise HTTPError(405, "method_not_allowed")

    def _create(self, st: State, body: Any):
        req_key = self.headers.get("Idempotency-Key")
        if not req_key:
            raise HTTPError(400, "missing_idempotency_key")
        key = req_key
        body = _validate(body)
        rr = (body.get("metadata") or {}).get("refund_request_id")
        corr = {"idempotency_key": key, "refund_request_id": rr}
        rh = request_hash(body)
        spec = st.faults.spec(key)
        conn = st.conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT request_hash, status, http_status, response_json FROM "
                               "idempotency WHERE key = ?", (key,)).fetchone()
            if row is None and st.faults.take(key, "drop_before_accept", spec):
                conn.execute("ROLLBACK")
                raise Drop(corr | {"fault": "drop_before_accept"})
            if row is None:
                conn.execute("INSERT INTO idempotency(key, request_hash, status, created_at) "
                             "VALUES (?, ?, 'processing', ?)", (key, rh, time.time()))
            conn.execute("COMMIT")
        except Drop:
            raise
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if row is not None:
            if row[0] != rh:
                raise HTTPError(422, "idempotency_key_reused", _corr=corr)
            if row[1] == "processing":
                raise HTTPError(409, "request_in_progress", _corr=corr)
            stored = json.loads(row[3])
            return int(row[2]), stored, corr | {"replayed": True,
                                                "emu_refund_id": stored.get("id")}
        time.sleep(float(spec.get("accept_delay") or 0))
        status, resp = self._accept(conn, key, body, rr, spec)
        time.sleep(float(spec.get("respond_delay") or 0))
        if status == 200 and st.faults.take(key, "drop_after_accept", spec):
            raise Drop(corr | {"fault": "drop_after_accept", "emu_refund_id": resp["id"]})
        return status, resp, corr | {"emu_refund_id": resp.get("id")}

    @staticmethod
    def _accept(conn, key: str, body: dict, rr, spec: dict) -> tuple[int, dict]:
        conn.execute("BEGIN IMMEDIATE")
        try:
            ch = conn.execute("SELECT amount_cents, payee_ref FROM charges WHERE charge_id = ?",
                              (body["charge_id"],)).fetchone()
            if ch is None:
                status, resp = 404, {"error": "charge_not_found"}
            else:
                used = conn.execute("SELECT coalesce(sum(amount_cents), 0) FROM refunds WHERE "
                                    "charge_id = ? AND state <> 'failed'",
                                    (body["charge_id"],)).fetchone()[0]
                if used + body["amount_cents"] > ch[0]:
                    status, resp = 409, {"error": "amount_exceeds_charge",
                                         "detail": f"{used} + {body['amount_cents']} > {ch[0]}"}
                else:
                    payee = ch[1]
                    seq = conn.execute("SELECT coalesce(max(seq), 0) + 1 FROM refunds"
                                       ).fetchone()[0]
                    rid = f"re_syn_{seq:06d}"
                    now = time.time()
                    fail = spec.get("fail")
                    planned = f"failed:{fail}" if fail else "succeeded"
                    settle = float(spec.get("settle_delay", DEFAULT_SETTLE_DELAY))
                    meta = json.dumps(body.get("metadata") or {}, sort_keys=True)
                    conn.execute(
                        "INSERT INTO refunds(id, seq, charge_id, amount_cents, payee_ref, "
                        "idempotency_key, refund_request_id, metadata_json, state, planned, "
                        "settle_after, created_at) VALUES (?,?,?,?,?,?,?,?, 'pending', ?,?,?)",
                        (rid, seq, body["charge_id"], body["amount_cents"], payee, key, rr, meta,
                         planned, now + settle, now))
                    conn.execute("INSERT INTO ledger_events(refund_id, from_state, to_state, "
                                 "reason, at) VALUES (?, NULL, 'pending', 'accepted', ?)",
                                 (rid, now))
                    status, resp = 200, L.get_refund(conn, refund_id=rid)[0]
            conn.execute("UPDATE idempotency SET status = 'done', http_status = ?, "
                         "response_json = ? WHERE key = ?",
                         (status, json.dumps(resp, sort_keys=True), key))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return status, resp


def _settler(st: State) -> None:
    conn = L.connect(st.ledger_path)
    while not st.stop.wait(0.05):
        try:
            L.settle_due(conn)
        except Exception:  # noqa: BLE001 — следующий тик повторит
            pass


def recover(ledger_path: Path) -> int:
    """После рестарта: маркеры `processing` без принятого возврата — запрос не был принят
    (сбой между маркером и принятием); повтор клиента с тем же ключом исполнит его заново."""
    conn = L.connect(ledger_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        n = conn.execute("DELETE FROM idempotency WHERE status = 'processing'").rowcount
        conn.execute("COMMIT")
        return n
    finally:
        conn.close()


def serve(ledger_path: str | Path, runtime_dir: str | Path, *, port: int = 0,
          fault_file: str | Path | None = None) -> None:
    ledger_path, rt = Path(ledger_path), Path(runtime_dir)
    if not ledger_path.exists():
        raise L.LedgerError(f"нет книги {ledger_path} (shop_slice reset)")
    rt.mkdir(parents=True, exist_ok=True)
    (rt / "ready.json").unlink(missing_ok=True)
    recovered = recover(ledger_path)
    c = L.connect(ledger_path)
    ver = c.execute("PRAGMA user_version").fetchone()[0]
    c.close()
    st = State(ledger_path, FaultBook(fault_file))
    srv = ServiceServer(Handler, port=port, name="pay-emu", log_path=rt / "requests.jsonl",
                        state=st)
    t = threading.Thread(target=_settler, args=(st,), daemon=True)
    t.start()
    srv.write_ready(rt / "ready.json", schema_version=ver, recovered_processing=recovered,
                    ledger=str(ledger_path))
    try:
        srv.serve_forever(poll_interval=0.05)
    finally:
        st.stop.set()
        srv.server_close()
