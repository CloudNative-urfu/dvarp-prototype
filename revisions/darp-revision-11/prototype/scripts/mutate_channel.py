"""Мутационная проверка channel: каждая мутация меняет одну строку в копии пакета во
временном каталоге и прогоняет test_channel.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_channel.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CH = "darp_range/channel/"
EL = "darp_range/event_log/"
MUTANTS = [
    ("research signal leaks in main mode", CH + "channel.py",
     "feedback = research_feedback(turn.signals) if self.research else None",
     "feedback = research_feedback(turn.signals)"),
    ("raw signals passed in research mode", CH + "channel.py",
     "    return MappingProxyType(out)", "    return MappingProxyType(dict(signals))"),
    ("research values unchecked", CH + "channel.py",
     "if not any(val is a or (type(val) is type(a) and val == a) for a in allowed):",
     "if False:"),
    ("bool/int confusion in research values", CH + "channel.py",
     "if not any(val is a or (type(val) is type(a) and val == a) for a in allowed):",
     "if not any(val == a for a in allowed):"),
    ("mode/log mismatch allowed", CH + "channel.py",
     'if (mode == "research") != log.research_mode:', "if False:"),
    ("research marker dropped from events", CH + "channel.py",
     '"direction": "to_defender",', '"direction": "to_defender", "mode": "main",'),
    ("turn limit not enforced", CH + "channel.py",
     "    if delivered >= max_user_turns:\n        return \"turn_limit_exceeded\"",
     "    if False:\n        return \"turn_limit_exceeded\""),
    ("turn limit off by one", CH + "channel.py",
     "if delivered >= max_user_turns:", "if delivered > max_user_turns:"),
    ("str subclass accepted from user", CH + "channel.py",
     "    if type(msg) is not str:", "    if not isinstance(msg, str):"),
    ("empty message accepted", CH + "channel.py", "    if not msg.strip():", "    if not msg:"),
    ("oversize accepted", CH + "channel.py",
     "if len(msg) > MAX_MESSAGE_CHARS:", "if len(msg) > 10 * MAX_MESSAGE_CHARS:"),
    ("control chars accepted", CH + "channel.py",
     'if any(unicodedata.category(c) == "Cc" and c not in _ALLOWED_CONTROL for c in msg):',
     "if False:"),
    ("violation delivered anyway", CH + "channel.py",
     '                status = "protocol_violation"\n                break',
     '                status = "protocol_violation"\n                violation = None'),
    ("defender gets extra context", CH + "channel.py",
     "turn = _defender_turn(defender.respond(msg))",
     "turn = _defender_turn(defender.respond(msg, **({'mode': self.mode} if False else {'episode': ep})))"),
    ("non-str defender output accepted", CH + "channel.py",
     "    if not isinstance(out, DefenderTurn):\n        raise", "    if False:\n        raise"),
    ("closed flag not type-checked", CH + "channel.py",
     "    if type(out.closed) is not bool:", "    if False:"),
    ("reply subclass accepted", CH + "channel.py",
     "    if type(out.reply) is not str:", "    if not isinstance(out.reply, str):"),
    ("defender reuse allowed", CH + "channel.py",
     "if id(defender) in self._defenders:", "if False:"),
    ("defender close ignored", CH + "channel.py",
     '                status = "defender_closed"\n                break',
     '                status = "defender_closed"'),
    ("on_close not called", CH + "channel.py",
     'on_close = getattr(user, "on_close", None)', "on_close = None"),
    ("reply not truncated", CH + "channel.py",
     "if len(reply) > MAX_REPLY_CHARS:", "if False:"),
    ("transcript skips customer", CH + "channel.py",
     '                    transcript.record("customer", msg)', "                    pass"),
    ("observation leaks signals", CH + "channel.py",
     "closed=turn.closed, research=feedback)",
     "closed=turn.closed, research=feedback if feedback is not None else MappingProxyType(dict(turn.signals)))"),
    ("user error swallowed", CH + "channel.py",
     '                close("user_error")\n                raise UserEndpointError',
     '                close("user_error")\n                return None\n                raise UserEndpointError'),
    ("defender_error not logged", CH + "channel.py",
     '                close("defender_error")', "                pass"),
    ("mixing allowed", CH + "channel.py", "    if len(modes) > 1:", "    if False:"),
    ("main log accepts research_feedback", EL + "log.py",
     "if kind in RESEARCH_ONLY_KINDS and not self.research_mode:", "if False:"),
    ("executor transcript ignores refusal", CH + "endpoints.py",
     "        if not getattr(res, \"ok\", getattr(res, \"status\", None) == \"ok\"):", "        if False:"),
    ("scripted user skips turn", CH + "endpoints.py", "        self._i += 1", "        self._i += 2"),
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
            try:
                r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".",
                                    "-p", "test_channel.py"], cwd=td, capture_output=True, timeout=120)
                ok = r.returncode != 0
            except subprocess.TimeoutExpired:
                ok = True  # зависание тоже обнаружено
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
