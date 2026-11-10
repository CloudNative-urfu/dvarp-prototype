"""Тестовая аутентификация контура (ревизия 11): НЕ IAM и не секрет.

`reset` создаёт случайный ключ контура в `.runtime/` и выдаёт токены субъектам:
`<role>.<subject>.<HMAC-SHA256(key, "role:subject")[:40]>`. Сервис по токену определяет роль
и субъекта (сравнение `hmac.compare_digest`); роль из тела запроса не используется никогда.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

ROLES = ("operator", "customer")


@dataclass(frozen=True)
class Principal:
    role: str
    subject: str


def new_key(path: str | Path) -> bytes:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key.hex().encode("ascii"))
    return key


def load_key(path: str | Path) -> bytes:
    return bytes.fromhex(Path(path).read_text(encoding="ascii").strip())


def _mac(key: bytes, role: str, subject: str) -> str:
    return hmac.new(key, f"{role}:{subject}".encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def issue(key: bytes, role: str, subject: str) -> str:
    if role not in ROLES or "." in subject:
        raise ValueError(f"нельзя выдать токен {role!r}/{subject!r}")
    return f"{role}.{subject}.{_mac(key, role, subject)}"


def verify(key: bytes, token: str | None) -> Principal | None:
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] not in ROLES:
        return None
    role, subject, mac = parts
    if not hmac.compare_digest(mac, _mac(key, role, subject)):
        return None
    return Principal(role, subject)


def issue_all(key: bytes, conn: sqlite3.Connection, out: str | Path) -> dict:
    tokens = {"operators": {o: issue(key, "operator", o) for (o,) in conn.execute(
                  "SELECT id FROM operators WHERE active = 1 ORDER BY id")},
              "customers": {c: issue(key, "customer", c) for (c,) in conn.execute(
                  "SELECT id FROM customers ORDER BY id")},
              "note": "synthetic test-slice tokens (not a secret, not IAM)"}
    Path(out).write_text(json.dumps(tokens, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return tokens
