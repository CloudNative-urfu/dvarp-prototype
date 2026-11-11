"""HTTP/JSON на 127.0.0.1: сервер с файлом готовности, клиент, журнал запросов (ревизия 11).

Сервер:
- слушает только `127.0.0.1` (никогда `0.0.0.0`); порт 0 — эфемерный;
- `ready.json` (`{port, pid, schema_version, ...}`) пишется атомарно ПОСЛЕ открытия и миграции
  хранилища и после `bind`: кто дождался файла, может слать запросы;
- если `127.0.0.1` нельзя занять (нет loopback, порт занят), — `LoopbackUnavailable` с
  причиной; процесс выходит с ненулевым кодом, файла готовности нет;
- обработчик может «оборвать» соединение без ответа (`Drop`) — так эмулятор моделирует
  потерю ответа до/после принятия операции;
- каждый запрос пишется строкой JSONL (`requests.jsonl` рядом с ready.json) с корреляционными
  id, которые обработчик вернул в `corr` (ticket_id, refund_request_id, idempotency_key).

Клиент `call` различает ответ сервера (любой HTTP-код) и сбой транспорта (`TransportError`:
отказ соединения, обрыв без ответа, тайм-аут): только по второму вызывающий не знает исхода.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

HOST = "127.0.0.1"
MAX_BODY = 64 * 1024


class LoopbackUnavailable(RuntimeError):
    """127.0.0.1 недоступен для bind (нет loopback в netns, порт занят)."""


class TransportError(ConnectionError):
    """Ответа нет: соединение не установлено, оборвано или истёк тайм-аут."""


class HTTPError(Exception):
    """Ответ обработчика с кодом ошибки: `raise HTTPError(409, "duplicate_request")`."""

    def __init__(self, status: int, code: str, detail: str = "", **extra: Any):
        super().__init__(f"{status} {code}")
        self.status, self.code, self.detail, self.extra = status, code, detail, extra

    def body(self) -> dict[str, Any]:
        b = {"error": self.code}
        if self.detail:
            b["detail"] = self.detail
        b.update(self.extra)
        return b


class Drop(Exception):
    """Закрыть соединение без ответа (инъекция сбоя оценщиком)."""


def write_json_atomic(path: str | Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def read_ready(path: str | Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class ServiceServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, handler_cls: type, *, port: int = 0, name: str, log_path: Path | None,
                 state: Any = None):
        self.name, self.state = name, state
        self._log_path = log_path
        self._log_lock = threading.Lock()
        try:
            super().__init__((HOST, port), handler_cls)
        except OSError as exc:
            raise LoopbackUnavailable(
                f"{name}: cannot bind {HOST}:{port} ({type(exc).__name__}: {exc}); loopback "
                f"is required (in a netns: `ip link set lo up`), or the port is busy") from exc
        # bind в netns с опущенным lo проходит, но соединиться нельзя: без самопроверки
        # процесс молча ждал бы запросов, которые никогда не придут.
        try:
            with socket.create_connection((HOST, self.port), timeout=2.0):
                pass
        except OSError as exc:
            self.server_close()
            raise LoopbackUnavailable(
                f"{name}: cannot bind {HOST}:{self.port} usefully — self-connect failed "
                f"({type(exc).__name__}: {exc}); loopback is required "
                f"(in a netns: `ip link set lo up`)") from exc

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def log_request_line(self, rec: Mapping[str, Any]) -> None:
        if self._log_path is None:
            return
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
        with self._log_lock:
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(line)

    def write_ready(self, path: str | Path, **extra: Any) -> None:
        write_json_atomic(path, {"port": self.port, "pid": os.getpid(), "host": HOST,
                                 "service": self.name, **extra})


class JSONHandler(BaseHTTPRequestHandler):
    """Базовый обработчик: подкласс реализует `route(method, parts, query, body)` →
    `(status, obj, corr)`; ошибки — `HTTPError`; обрыв — `Drop`."""

    server: ServiceServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — тишина в stderr
        return

    def _read_body(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise HTTPError(413, "body_too_large")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HTTPError(400, "invalid_json") from None

    def _handle(self, method: str) -> None:
        t0 = time.time()
        u = urlsplit(self.path)
        parts = [p for p in u.path.split("/") if p]
        query = {k: v[-1] for k, v in parse_qs(u.query).items()}
        corr: dict[str, Any] = {}
        status, dropped = 0, False
        try:
            body = self._read_body() if method == "POST" else None
            status, obj, corr = self.route(method, parts, query, body)
        except Drop as d:
            dropped, corr = True, (d.args[0] if d.args else {})
        except HTTPError as e:
            status, obj, corr = e.status, e.body(), dict(e.extra.get("_corr", {}))
            obj.pop("_corr", None)
        except Exception as exc:  # noqa: BLE001 — ответ 500, не падение процесса
            status, obj = 500, {"error": "internal_error", "detail": type(exc).__name__}
        self.server.log_request_line({
            "ts": round(t0, 4), "svc": self.server.name, "method": method, "path": u.path,
            "query": query or None, "status": None if dropped else status, "dropped": dropped,
            "ms": round((time.time() - t0) * 1000, 1), **{k: v for k, v in corr.items()
                                                          if v is not None}})
        if dropped:
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return
        data = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def route(self, method: str, parts: list[str], query: dict[str, str],
              body: Any) -> tuple[int, Any, dict[str, Any]]:
        raise HTTPError(404, "not_found")


def call(port: int, method: str, path: str, body: Any = None, *,
         headers: Mapping[str, str] | None = None, timeout: float = 10.0
         ) -> tuple[int, Any]:
    """Один HTTP-запрос к 127.0.0.1:port. Ответ — `(status, json)`; нет ответа —
    `TransportError` (вызывающий НЕ знает, была ли операция принята)."""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Connection": "close", **(headers or {})}
    conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        status = resp.status
    except (OSError, http.client.HTTPException) as exc:
        raise TransportError(f"{method} {path}: {type(exc).__name__}: {exc}") from exc
    finally:
        conn.close()
    try:
        return status, (json.loads(raw.decode("utf-8")) if raw else None)
    except ValueError:
        return status, {"raw": raw[:200].decode("utf-8", "replace")}
