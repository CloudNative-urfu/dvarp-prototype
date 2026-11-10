"""Ревизия 3 (K3.7): условия окружения для тестов, зависящих от песочницы.

Пропуск — только при доказанном отсутствии условия и с явной причиной; verify перечисляет
все пропуски в `environment.json` (не скрывает). Рабочая изоляция без пропусков —
`scripts/isolated_verify.sh` (сетевое пространство без внешних интерфейсов, поднятый lo,
непривилегированный uid во вложенном user namespace).
"""

from __future__ import annotations

import os
import socket
import sqlite3
import tempfile
from pathlib import Path


def loopback_available() -> bool:
    """Можно ли поднять TCP-сервер на 127.0.0.1 и подключиться к нему (lo поднят)."""
    try:
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            with socket.create_connection(srv.getsockname(), timeout=1):
                return True
    except OSError:
        return False


def file_permissions_enforced() -> bool:
    """Запрещает ли ОС запись в файл 0o444 текущему процессу (у root в userns — нет)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ro.sqlite"
        sqlite3.connect(p).close()
        os.chmod(p, 0o444)
        try:
            with open(p, "ab"):
                return False
        except PermissionError:
            return True
        finally:
            os.chmod(p, 0o644)


LOOPBACK_SKIP = "loopback interface is down in this network namespace (e.g. bare `unshare -rn`)"
PERM_SKIP = "file permission bits are not enforced for this uid (root / uid 0 in a user namespace)"
