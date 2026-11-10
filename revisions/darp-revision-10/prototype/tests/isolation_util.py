"""Общие помощники тестов изоляции (ревизия 9)."""

import functools
import shutil
import subprocess
import tempfile


@functools.lru_cache(maxsize=1)
def bwrap_works() -> bool:
    """bubblewrap есть и запускает непривилегированный sandbox (user namespaces)."""
    b = shutil.which("bwrap")
    if not b:
        return False
    try:
        p = subprocess.run([b, "--unshare-all", "--ro-bind", "/", "/", "true"],
                           capture_output=True, timeout=20, env={})
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0


SKIP_MSG = ("bubblewrap/user namespaces недоступны: sandbox-проверка не выполнена "
            "(это SKIP, не PASS; прогон получает claim_level in_process_research)")


def tmpdir(test) -> str:
    d = tempfile.mkdtemp(prefix="darp-test-iso-")
    test.addCleanup(shutil.rmtree, d, True)
    return d
