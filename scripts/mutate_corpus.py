"""Мутационная проверка тестов корпуса: каждая мутация — одна замена строки в копии
рабочего каталога; мутант должен «убиваться» (test_corpus.py падает).

Запуск: python3 scripts/mutate_corpus.py [корень]  → код 0, если убиты все мутанты.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MUTANTS = [
    ("volume ratio disabled", "darp_range/corpus/schema.py",
     "VOLUME_RATIO = (0.5, 2.0)", "VOLUME_RATIO = (0.0, 1e9)"),
    ("pair partner check off", "darp_range/corpus/schema.py",
     "if set(pair_attacks) != set(controls):", "if False:"),
    ("dirty style gap ignored", "darp_range/corpus/schema.py",
     "DIRTY_MAX_GAP = 0.15", "DIRTY_MAX_GAP = 1.0"),
    ("dirty rate ignored", "darp_range/corpus/schema.py",
     "DIRTY_MIN_RATE = 0.8", "DIRTY_MIN_RATE = 0.0"),
    ("pii patterns off", "darp_range/corpus/schema.py",
     "if rx.search(stripped):", "if False:"),
    ("ascii check off", "darp_range/corpus/schema.py",
     'if not all(c == "\\n" or 32 <= ord(c) < 127 for c in t):', "if False:"),
    ("shift may expect review", "darp_range/corpus/schema.py",
     'if goal == "shift" and exp.get("task", {}).get("kind") == "manual_review":', "if False:"),
    ("universal predicates dropped", "darp_range/corpus/bind.py",
     "forbidden = specific + [u for u in universal if u not in specific]",
     "forbidden = specific"),
    ("max amount ignores qty", "darp_range/corpus/bind.py",
     'max_amount_cents=it["price_cents"] * it["qty"])', 'max_amount_cents=it["price_cents"])'),
    ("task self-check off", "darp_range/corpus/bind.py",
     "    if hits:\n", "    if False:\n"),
    ("attacker pool = legal", "darp_range/corpus/bind.py",
     'pool = attackers if kind == "attack" else legal', "pool = legal"),
    ("history marked trusted", "darp_range/corpus/bind.py",
     '"provenance": "ticket_history"', '"provenance": "trusted_policy"'),
    ("ground truth leaks to fixture", "darp_range/corpus/bind.py",
     'fixture: dict[str, Any] = {"tickets": [ticket]}',
     'fixture: dict[str, Any] = {"tickets": [ticket]}; ticket["objective"] = entry.get("objective")'),
    ("file hash not checked", "darp_range/corpus/store.py",
     'if actual != manifest.get("sha256"):', "if False:"),
    ("entry hashes not checked", "darp_range/corpus/store.py",
     'if hashes != manifest.get("entry_hashes"):', "if False:"),
    ("split filter ignored", "darp_range/corpus/store.py",
     'entries = [e for e in entries if e["split"] == split]', "pass"),
    ("holdout rule changed", "darp_range/corpus/entries.py",
     "if i % 3 == 2)", "if i % 3 == 5)"),
    ("wrong ground truth in source", "darp_range/corpus/entries.py",
     'refusal("duplicate_request")', 'refusal("outside_window")'),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    survived = 0
    for name, rel, old, new in MUTANTS:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for part in ("darp_range", "tests", "corpus"):
                shutil.copytree(root / part, d / part,
                                ignore=shutil.ignore_patterns("__pycache__"))
            p = d / rel
            s = p.read_text()
            if old not in s:
                print(f"BROKEN MUTANT (нет строки): {name}")
                survived += 1
                continue
            p.write_text(s.replace(old, new, 1))
            r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                                "-t", ".", "-p", "test_corpus.py"], cwd=d,
                               capture_output=True, text=True)
            status = "killed" if r.returncode != 0 else "SURVIVED"
            survived += r.returncode == 0
            print(f"{status}: {name}")
    print(f"{len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
