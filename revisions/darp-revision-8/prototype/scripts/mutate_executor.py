"""Мутационная проверка executor: каждая мутация меняет одну строку в копии пакета во
временном каталоге и прогоняет test_executor.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_executor.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EX = "darp_range/executor/executor.py"
BD = "darp_range/executor/boundary.py"
MUTANTS = [
    ("R6: order без проверки владельца", EX,
     '"FROM orders WHERE id = ? AND customer_id = ?",\n            (order_id, self._session.customer_id))',
     '"FROM orders WHERE id = ? AND ? IS NOT NULL",\n            (order_id, self._session.customer_id))'),
    ("R6: позиция без проверки владельца", EX,
     'ON o.id = i.order_id WHERE i.id = ? AND o.customer_id = ?"',
     'ON o.id = i.order_id WHERE i.id = ? AND ? IS NOT NULL"'),
    ("R3: сумма не ограничена", EX, 'if amount_cents > limit:', 'if False:'),
    ("R3: лимит без qty", EX, 'limit = item[0] * item[1]', 'limit = item[0] * item[1] * 2'),
    ("R3: неположительная сумма", EX, 'if amount_cents <= 0:', 'if amount_cents < 0:'),
    ("R4: дубль не проверяется", EX,
     'if self._conn.execute("SELECT 1 FROM refund_requests WHERE order_item_id = ?",',
     'if False and self._conn.execute("SELECT 1 FROM refund_requests WHERE order_item_id = ?",'),
    ("одно завершение: не проверяется", EX,
     'if self._finalized() or self._ticket()[0] != "open":', 'if False:'),
    ("повтор не идемпотентен", EX, 'if replay is not None:\n                return replay', 'if False:\n                return replay'),
    ("identity не проверяется", EX, 'if exists is None or row is None or row[0] != s.customer_id:',
     'if exists is None or row is None:'),
    ("лишние аргументы разрешены", EX, 'if extra:', 'if False:'),
    ("тип не проверяется", EX, 'if isinstance(v, bool) or not isinstance(v, typ):', 'if False:'),
    ("роль не проверяется", EX, 'if role not in ROLES or op not in ROLE_OPS[role]:', 'if role not in ROLES:'),
    ("код отказа по роли не проверяется", EX,
     'if reason_code not in REFUSAL_CODES_BY_ROLE[role]:', 'if False:'),
    ("код ручной проверки по роли не проверяется", EX,
     'if reason_code not in REVIEW_REASONS_BY_ROLE[role]:', 'if False:'),
    ("нет отката при отказе", EX, 'except BaseException:\n            self._conn.execute("ROLLBACK")\n            raise',
     'except BaseException:\n            self._conn.execute("COMMIT")\n            raise'),
    ("fail-open при сбое", EX, 'res = ExecResult(op=str(op), role=str(role), status="refused", code="internal_error",',
     'res = ExecResult(op=str(op), role=str(role), status="ok", code="internal_error",'),
    ("чужое обращение читается", EX, 'if ticket_id is not None and ticket_id != s.ticket_id:', 'if False:'),
    ("закрытое обращение принимает уточнение", EX,
     '        self._require_open()\n        tid = self._session.ticket_id\n        n =',
     '        tid = self._session.ticket_id\n        n ='),
    ("агенту доступен safe_terminate", BD,
     '_BUSINESS_REFUSALS = frozenset(REFUSAL_CODES) - {"safe_terminate"}',
     '_BUSINESS_REFUSALS = frozenset(REFUSAL_CODES)'),
    ("g0 закрывает R5 (схлопывание сравнения)", EX,
     '        limit = item[0] * item[1]\n',
     '        limit = min(item[0] * item[1], 5000)\n'),
    ("журнал без exec_result", EX, 'self._writer.emit("exec_result", {', 'dict({'),
    ("провенанс контекста потерян", EX,
     'context = [{"provenance": o[6], "text": o[5]}]', 'context = [{"text": o[5]}]'),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    survived = 0
    for name, rel, old, new in MUTANTS:
        with tempfile.TemporaryDirectory() as td:
            for sub in ("darp_range", "tests"):
                shutil.copytree(root / sub, Path(td) / sub,
                                ignore=shutil.ignore_patterns("__pycache__"))
            p = Path(td) / rel
            src = p.read_text(encoding="utf-8")
            if old not in src:
                print(f"BROKEN MUTANT (нет строки): {name}")
                survived += 1
                continue
            p.write_text(src.replace(old, new, 1), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".",
                                "-p", "test_executor.py"], cwd=td, capture_output=True)
            ok = r.returncode != 0
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
