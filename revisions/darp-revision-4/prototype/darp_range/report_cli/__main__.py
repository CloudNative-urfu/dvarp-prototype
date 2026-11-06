"""CLI сквозного прогона полигона (NFR-2.14, NFR-2.5).

    # офлайн: все замороженные конфигурации, заглушка модели, запрет сети, таблица метрик и L*
    python3 -m darp_range.report_cli offline --out runs/e2e [--split dev] [--seeds 7,8,9]
        [--configs g0,g_in,...] [--entries ...] [--blocks K] [--n-legal N] [--w-a 0.2]
        [--attack-mode D|A] [--replay none|first_block|all] [--l-viol 100,1000]

    # живой: модель агента через RESEARCH_LLM_*, жёсткие лимиты на весь прогон
    python3 -m darp_range.report_cli live --out runs/live1 [--configs g0,g_in]
        [--entries atk-02,ctl-02] [--max-requests 60] [--max-tokens 150000]
        [--max-episodes 16] [--max-wall-s 900]

    # повторная проверка готового каталога без нового прогона
    python3 -m darp_range.report_cli verify --out runs/e2e

Коды выхода: 0 — успех, 1 — самопроверки не прошли, 2 — ошибка конфигурации/бюджета/
каталога (прогон не начат).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from darp_range.llm import LLMConfigError
from darp_range.runner import RunnerError, RunPlan

from .live import LiveBudget
from .pipeline import PipelineError, run_live, run_offline, verify_output

# План живого прогона по умолчанию: одна пара «атака + её чистый контроль», 2 конфигурации,
# без повтора — 8 эпизодов с моделью агента (≤ 16 по бюджету).
LIVE_DEFAULT_CONFIGS = "g0,g_in"
LIVE_DEFAULT_ENTRIES = "atk-02,ctl-02"


def _floats(s: str | None):
    return None if not s else tuple(float(x) for x in s.split(","))


def _plan(a: argparse.Namespace, *, live: bool) -> RunPlan:
    entries = a.entries or (LIVE_DEFAULT_ENTRIES if live else None)
    mode = getattr(a, "attack_mode", "D")
    # Явный список записей берётся из всего корпуса; без него офлайн по умолчанию — срез dev.
    split = a.split or (None if entries or live else "dev")
    return RunPlan(split=split, entries=tuple(entries.split(",")) if entries else None,
                   n_legal=a.n_legal, w_a=a.w_a, blocks=a.blocks, attack_mode=mode,
                   attack_strategy="corpus" if mode == "D" else "llm",
                   agent_mode="live" if live else "stub", replay=a.replay,
                   master_seed=getattr(a, "seed", 7), mean_gap=a.mean_gap,
                   service_mode=a.service_mode, operators=a.operators,
                   attack_max_attempts=getattr(a, "attack_max_attempts", 3),
                   guard_fault_rate=getattr(a, "guard_fault_rate", 0.0),
                   corpus_dir=getattr(a, "corpus", None),
                   agent_profile=getattr(a, "agent_profile", None) or "default")


def _print(res: dict) -> None:
    print(f"{'config':38s} {'status':18s} {'L* base':>12s} {'L* low':>12s} {'L* high':>12s}")
    for n, r in res["summary"]["configs"].items():
        f = lambda v: "—" if v is None else f"{v:.6g}"  # noqa: E731
        print(f"{n:38s} {r['status']:18s} {f(r['L_star']):>12s} "
              f"{f(r['L_star_low_prices']):>12s} {f(r['L_star_high_prices']):>12s}")
    for n, ok in res["checks"]:
        print(("PASS " if ok else "FAIL ") + n)
    print(f"report: {res['out']}/report.md  ok={res['ok']}  elapsed={res['elapsed_s']}s")


def cmd_offline(a: argparse.Namespace) -> int:
    seeds = tuple(int(x) for x in a.seeds.split(","))
    res = run_offline(a.out, _plan(a, live=False), seeds=seeds,
                      configs=a.configs.split(",") if a.configs else None,
                      l_viol=_floats(a.l_viol))
    res["out"] = str(a.out)
    _print(res)
    return 0 if res["ok"] else 1


def cmd_live(a: argparse.Namespace) -> int:
    budget = LiveBudget(max_requests=a.max_requests, max_tokens=a.max_tokens,
                        max_requests_per_episode=a.max_requests_per_episode,
                        max_episodes=a.max_episodes, max_wall_s=a.max_wall_s,
                        timeout_s=a.timeout_s)
    res = run_live(a.out, _plan(a, live=True), budget,
                   configs=(a.configs or LIVE_DEFAULT_CONFIGS).split(","),
                   l_viol=_floats(a.l_viol))
    res["out"] = str(a.out)
    _print(res)
    m = res["live"]["meter"]
    print(f"live: requests={m['used']['requests']}/{m['budget']['max_requests']} "
          f"tokens={m['used']['tokens_in'] + m['used']['tokens_out']} "
          f"refused={m['refused_before_send']} degraded={res['live']['degraded']['total']} "
          f"defects={res['defects']}")
    return 0 if res["ok"] else 1


def cmd_verify(a: argparse.Namespace) -> int:
    v = verify_output(a.out)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 0 if v["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.report_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("offline", "live"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--configs")
        p.add_argument("--split", choices=("dev", "holdout"),
                       help="срез корпуса; по умолчанию dev (офлайн без --entries)")
        p.add_argument("--entries")
        p.add_argument("--blocks", type=int)
        p.add_argument("--n-legal", type=int)
        p.add_argument("--w-a", type=float, default=0.2 if name == "offline" else 0.5)
        p.add_argument("--replay", choices=("none", "first_block", "all"),
                       default="first_block" if name == "offline" else "none")
        p.add_argument("--l-viol", help="иллюстративные цены нарушения через запятую, USD")
        # Нагрузка на общую очередь (§11 п. 5): интервал прибытия легальных обращений, режим
        # времени обслуживания оператора и число операторов.
        p.add_argument("--mean-gap", type=float, default=3.0,
                       help="средний интервал прибытия легальных обращений, мин")
        p.add_argument("--service-mode", choices=("fast", "base", "slow"), default="base",
                       help="время обслуживания оператора: 4 / 8 / 15 мин")
        p.add_argument("--operators", type=int, default=1)
        p.add_argument("--attack-mode", choices=("D", "A"), default="D",
                       help="D — корпус; A — адаптивный LLM-атакующий (живой: та же модель и "
                            "общий бюджет запросов)")
        p.add_argument("--attack-max-attempts", type=int, default=3, help="только режим A")
        p.add_argument("--guard-fault-rate", type=float, default=0.0,
                       help="доля смоделированных сбоев вызовов гвардрейлов (fail-open/closed)")
        p.add_argument("--agent-profile", default="default",
                       help="профиль заглушки агента (офлайн): default | claims | hardened | "
                            "claims_hardened (ревизия 3, K3.2)")
        p.add_argument("--corpus", help="каталог замороженного корпуса относительно корня "
                                        "(по умолчанию corpus/; holdout v2 — corpus/holdout_v2)")
        if name == "offline":
            p.add_argument("--seeds", default="7", help="master seed'ы через запятую")
        else:
            p.add_argument("--seed", type=int, default=7)
            p.add_argument("--max-requests", type=int, default=60)
            p.add_argument("--max-tokens", type=int, default=150_000)
            p.add_argument("--max-requests-per-episode", type=int, default=12)
            p.add_argument("--max-episodes", type=int, default=16)
            p.add_argument("--max-wall-s", type=float, default=900.0)
            p.add_argument("--timeout-s", type=float, default=60.0)
    p = sub.add_parser("verify")
    p.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        return {"offline": cmd_offline, "live": cmd_live, "verify": cmd_verify}[a.cmd](a)
    except (PipelineError, RunnerError, LLMConfigError, FileNotFoundError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
