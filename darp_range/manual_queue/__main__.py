"""CLI очереди ручных разборов.

    python3 -m darp_range.manual_queue demo [--seed 7] [--out FILE.json] [--log FILE.jsonl]

`demo` строит синтетический поток (24 легальных, w_A=0.2), помечает эскалации по
детерминированному профилю (ложные эскалации легальных и эскалации атак цели `shift`),
прогоняет пары «без атак / с атаками» по сетке 4/8/15 мин × 1/2 оператора и печатает
таблицу. Код 0 — в базовом режиме (1 оператор, 8 мин) эскалации атак измеримо задержали
легальный поток, ни в одном режиме не ускорили его, и эффект не убывает с ростом времени
обслуживания. Нулевой эффект при низкой загрузке (2 оператора, 4 мин) — честное свойство
очереди, а не сбой.
Профиль эскалаций — иллюстрация модели очереди, а не результат агента (см. simplifications).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from darp_range.event_log import EventLog
from darp_range.repro import make_rng

from .analysis import sensitivity
from .arrivals import build_flow
from .model import CaseRecord, ManualQueue, QueueConfig

LEGAL_FP_RATE = 0.15      # доля ложных эскалаций легальных обращений в демо
ATTACK_ESC_RATE = 0.6     # доля атак, отправленных к оператору, в демо
AUTO_MIN = {"legal": 3.0, "attack": 4.0}


def demo_cases(seed: int) -> tuple[list[CaseRecord], list[CaseRecord]]:
    legal, attack = [], []
    for s in build_flow(seed):
        rng = make_rng(seed, "manual_queue", "demo_escalation", s.case_id)
        rate = LEGAL_FP_RATE if s.traffic == "legal" else ATTACK_ESC_RATE
        c = CaseRecord(s.case_id, s.traffic, s.t_arrival, AUTO_MIN[s.traffic], rng.random() < rate,
                       account="atk-acct" if s.traffic == "attack" else f"cust-{s.index:03d}",
                       episode_id=s.case_id, reason="demo")
        (legal if s.traffic == "legal" else attack).append(c)
    return legal, attack


def cmd_demo(args: argparse.Namespace) -> int:
    legal, attack = demo_cases(args.seed)
    rows = sensitivity(legal, attack)
    print(f"seed={args.seed} legal={len(legal)} (escalated {sum(c.escalated for c in legal)}) "
          f"attack={len(attack)} (escalated {sum(c.escalated for c in attack)})")
    print(f"{'ops':>3} {'mode':>8} {'load':>4} {'svc':>5} {'Q_base':>8} {'Q_att':>8} {'dQ':>8} "
          f"{'maxdep':>6} {'util':>5} {'missed':>6}")
    for r in rows:
        print(f"{r['operators']:>3} {r['mode']:>8} {r['load']:>4} {r['service_minutes']:>5.1f} "
              f"{r['Q_base']:>8.2f} {r['Q_attacked']:>8.2f} {r['delta_Q']:>8.2f} "
              f"{r['max_depth']:>6} {r['utilization']:>5.2f} {r['expected_missed_attacks']:>6.2f}")
    if args.log:
        log = EventLog(f"manual_queue-demo-{args.seed}", sink=args.log)
        q = ManualQueue(QueueConfig(), writer=log.writer("manual_queue"))
        for c in legal + attack:
            log.start_episode(c.episode_id, c.traffic)
            q.add(c, in_episode=True)
            log.end_episode(c.episode_id)
        q.run()
        log.close()
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"seed": args.seed, "rows": rows}, indent=2,
                                             ensure_ascii=False), encoding="utf-8")
    all_rows = rows
    rows = [r for r in rows if r["load"] == "x1" and not r["degraded"]]
    base = [r for r in rows if r["operators"] == 1 and r["mode"] == "base"]
    # нагрузка ×3 и деградация ревьюера не уменьшают эффект базовой нагрузки
    x3 = {(r["operators"], r["mode"]): r for r in all_rows if r["load"] == "x3"}
    heavier = all(x3[(r["operators"], r["mode"])]["Q_attacked"] >= r["Q_attacked"] - 1e-9
                  for r in rows if (r["operators"], r["mode"]) in x3)
    monotone = heavier
    for c in {r["operators"] for r in rows}:
        seq = [r["delta_Q"] for r in sorted((r for r in rows if r["operators"] == c),
                                             key=lambda r: r["service_minutes"])]
        monotone &= all(a <= b + 1e-9 for a, b in zip(seq, seq[1:]))
    ok = bool(base) and base[0]["delta_Q"] > 0 and all(r["delta_Q"] >= -1e-9 for r in rows) and monotone
    print("OK: attack escalations measurably delay legal flow (base mode), effect grows with service time"
          if ok else "FAIL: expected queue effect not observed")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m darp_range.manual_queue")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="парные прогоны очереди по сетке чувствительности")
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--out")
    d.add_argument("--log")
    args = p.parse_args(argv)
    return cmd_demo(args)


if __name__ == "__main__":
    sys.exit(main())
