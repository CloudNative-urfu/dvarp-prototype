"""Мутационная проверка event_log: каждая мутация меняет одну строку в копии пакета во
временном каталоге и прогоняет test_event_log.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_event_log.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EL = "darp_range/event_log/"
MUTANTS = [
    ("guard → attacker side", EL + "schema.py",
     'return "defender" if cls == "guard" else', 'return "attacker" if cls == "guard" else'),
    ("manual_queue → harness", EL + "schema.py", '"manual_queue": "defender"', '"manual_queue": "harness"'),
    ("attacker counted as defender", EL + "costs.py",
     '_add(by_side[side], cost)', '_add(by_side["defender" if side == "attacker" else side], cost)'),
    ("decision basis not required", EL + "schema.py", 'if kind in DECISION_KINDS:', 'if False:'),
    ("negative cost allowed", EL + "schema.py", 'or value < 0:', 'or value < -1e18:'),
    ("unknown kind allowed", EL + "log.py", 'if kind not in KINDS[cls]:', 'if False:'),
    ("closed episode writable", EL + "log.py", 'if not ep.open:', 'if False:'),
    ("chain ignores body", EL + "log.py",
     'return hash_obj({"prev": prev, "body": dict(body)})', 'return hash_obj({"prev": prev})'),
    ("trajectory keeps wall_ms", EL + "log.py",
     'steps.append(strip_volatile(step, TRAJECTORY_VOLATILE))', 'steps.append(step)'),
    ("trajectory drops cost", EL + "log.py",
     'step["step"] = i', 'step["step"] = i; step.pop("cost", None)'),
    ("gpu not priced", EL + "costs.py",
     '+ cost.get("gpu_ms", 0.0) * rates.gpu_usd_per_hour / MS_PER_HOUR)', ')'),
    ("guard bucket missing", EL + "costs.py",
     'return "guard" if component.startswith(GUARD_PREFIX) else component', 'return component'),
    ("research mode mixed", EL + "costs.py", 'if len(modes) > 1:', 'if False:'),
    ("measure skips error cost", EL + "log.py", 'if error_kind is not None:', 'if False:'),
    ("score range unchecked", EL + "schema.py", 'or not 0.0 <= s <= 1.0:', ':'),
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
                                "-p", "test_event_log.py"], cwd=td, capture_output=True)
            ok = r.returncode != 0
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
