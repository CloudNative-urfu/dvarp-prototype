"""Remove run-owned temporary DB trees, including read-only snapshots on Windows."""

import os
import shutil
import stat
from pathlib import Path


def remove_temporary_tree(path: Path) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except PermissionError:
        # Run-owned trees only: add the write bit (other permission bits are kept),
        # then remove again. Explicit handling instead of the deprecated `onerror=`.
        for dirpath, dirnames, filenames in os.walk(path):
            for name in dirnames + filenames:
                target = Path(dirpath) / name
                try:
                    os.chmod(target, os.stat(target).st_mode | stat.S_IWRITE)
                except OSError:
                    pass
        shutil.rmtree(path)
