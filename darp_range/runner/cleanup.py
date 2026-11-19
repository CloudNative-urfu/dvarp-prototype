"""Remove run-owned temporary DB trees, including read-only snapshots on Windows."""

import os
import shutil
import stat
from pathlib import Path


def remove_temporary_tree(path: Path) -> None:
    def retry_readonly(function, filename, exc_info):
        if not isinstance(exc_info[1], PermissionError):
            raise exc_info[1]
        os.chmod(filename, os.stat(filename).st_mode | stat.S_IWRITE)
        function(filename)

    if path.exists():
        shutil.rmtree(path, onerror=retry_readonly)
