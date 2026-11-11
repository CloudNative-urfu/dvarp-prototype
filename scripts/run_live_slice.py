"""run_command ревизии 2: ограниченный живой срез и сводка путей завершения (K2.3).

1. `report_cli live` через RESEARCH_LLM_* на g0 × atk-02, ctl-01, ctl-02, ctl-04 с жёсткими
   лимитами (60 запросов, 150000 токенов, 16 эпизодов, 900 с, 12 запросов на эпизод) — лимиты
   действуют внутри report_cli на общем счётчике и не обходятся;
2. `scripts/live_slice_summary.py` по журналам прогона: завершения после ответов фактами,
   через согласованное подтверждение, отказы, turn_limit, ошибки провайдера.

Критерий успеха не задаётся (предрегистрация R2-H3): ноль завершений — допустимый результат.
Код выхода — код живого прогона (сбой лимита/провайдера не маскируется).

    python3 scripts/run_live_slice.py --out runs/live
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = "g0"
ENTRIES = "atk-02,ctl-01,ctl-02,ctl-04"
LIMITS = ["--max-requests", "60", "--max-tokens", "150000", "--max-episodes", "16",
          "--max-wall-s", "900", "--max-requests-per-episode", "12"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("runs/live"))
    a = ap.parse_args(argv)
    live = subprocess.run([sys.executable, "-m", "darp_range.report_cli", "live", "--out",
                           str(a.out), "--configs", CONFIGS, "--entries", ENTRIES, *LIMITS],
                          cwd=ROOT)
    run_dir = a.out if a.out.is_absolute() else ROOT / a.out
    if run_dir.exists():
        subprocess.run([sys.executable, "scripts/live_slice_summary.py", str(run_dir), "--out",
                        str(run_dir / "live_slice_summary.json")], cwd=ROOT)
    return live.returncode


if __name__ == "__main__":
    sys.exit(main())
