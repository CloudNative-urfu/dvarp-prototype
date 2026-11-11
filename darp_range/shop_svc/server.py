"""Процесс shop-svc: HTTP на 127.0.0.1 над БД контура v3 (ревизия 11).

- `POST /v1/operator/tickets/{id}/approve` `{order_item_id, amount_cents, defect_verified?,
  reason_code?}` и `/reject` `{reason_code}` — только токен оператора; `Idempotency-Key`
  обязателен; роль/поля субъекта в теле игнорируются;
- `GET /v1/operator/tickets/{id}` — просмотр кейса оператором;
- `GET /v1/customer/tickets/{id}` — статус для клиента-владельца (токен клиента; чужое → 404).
Без токена/с неверным токеном → 401, не та роль → 403; запись не выполняется.
"""

from __future__ import annotations

import threading
from pathlib import Path

from darp_range.svckit import HTTPError, JSONHandler, ServiceServer

from . import service
from .auth import Principal, load_key, verify
from .migrate import SLICE_SCHEMA_VERSION, open_slice


class State:
    def __init__(self, db: Path, key: bytes):
        self.db, self.key = db, key
        self.local = threading.local()

    def conn(self):
        c = getattr(self.local, "conn", None)
        if c is None:
            c = self.local.conn = open_slice(self.db)
        return c


class Handler(JSONHandler):
    def _principal(self, st: State) -> Principal:
        auth = self.headers.get("Authorization") or ""
        token = auth[7:].strip() if auth.startswith("Bearer ") else None
        who = verify(st.key, token)
        if who is None:
            raise HTTPError(401, "unauthorized")
        return who

    def route(self, method, parts, query, body):
        st: State = self.server.state
        if parts == ["v1", "health"] and method == "GET":
            return 200, {"ok": True}, {}
        if len(parts) >= 4 and parts[:3] == ["v1", "operator", "tickets"]:
            tid = parts[3]
            corr = {"ticket_id": tid}
            principal = self._principal(st)
            claims = principal    # роль и субъект — только из токена, тело не читается
            if claims.role != "operator":
                raise HTTPError(403, "forbidden", _corr=corr)
            conn = st.conn()
            if not conn.execute("SELECT 1 FROM operators WHERE id = ? AND active = 1",
                                (claims.subject,)).fetchone():
                raise HTTPError(403, "forbidden", _corr=corr)
            who = Principal("operator", claims.subject)
            if method == "GET" and len(parts) == 4:
                return 200, service.operator_view(conn, tid), corr
            if method == "POST" and len(parts) == 5 and parts[4] in ("approve", "reject"):
                key = self.headers.get("Idempotency-Key")
                if not key:
                    raise HTTPError(400, "missing_idempotency_key", _corr=corr)
                op = service.approve if parts[4] == "approve" else service.reject
                return op(conn, who, tid, body, key)
            raise HTTPError(404, "not_found", _corr=corr)
        if len(parts) == 4 and parts[:3] == ["v1", "customer", "tickets"] and method == "GET":
            principal = self._principal(st)
            if principal.role != "customer":
                raise HTTPError(403, "forbidden")
            res = service.customer_status(st.conn(), principal.subject, parts[3])
            return 200, res, {"ticket_id": parts[3], "customer_status": res["status"]}
        raise HTTPError(404, "not_found")


def serve(db: str | Path, runtime_dir: str | Path, key_file: str | Path, *,
          port: int = 0) -> None:
    db, rt = Path(db), Path(runtime_dir)
    rt.mkdir(parents=True, exist_ok=True)
    (rt / "ready.json").unlink(missing_ok=True)
    open_slice(db).close()        # миграция проверена до готовности
    st = State(db, load_key(key_file))
    srv = ServiceServer(Handler, port=port, name="shop-svc", log_path=rt / "requests.jsonl",
                        state=st)
    srv.write_ready(rt / "ready.json", schema_version=SLICE_SCHEMA_VERSION, db=str(db))
    try:
        srv.serve_forever(poll_interval=0.05)
    finally:
        srv.server_close()
