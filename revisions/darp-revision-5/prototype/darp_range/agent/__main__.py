"""CLI компонента agent: `python3 -m darp_range.agent spec` — декларация агента (промпт,
инструменты, параметры цикла, профиль заглушки); офлайн.

Офлайн-demo по корпусу и живой прогон с жёсткими лимитами — `python3 -m darp_range.agent_demo`
(обвязка с гвардрейлами вынесена из пакета агента, FR-1.9).
"""

from __future__ import annotations

import argparse
import json
import sys

from .agent import agent_spec
from .stub import DEFAULT_PROFILE, HARDENED_PROFILE, VULNERABILITIES


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("spec")
    ap.parse_args(argv)
    print(json.dumps({"agent": agent_spec(), "stub_profile": DEFAULT_PROFILE,
                      "vulnerabilities": VULNERABILITIES,
                      "hardened_profile": HARDENED_PROFILE["vulnerabilities"]},
                     ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
