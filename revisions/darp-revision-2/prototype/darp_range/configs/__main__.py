"""CLI компонента `configs` (офлайн, без сети и модельных API).

- `list` — предзарегистрированный набор: семейство, роль, гвардрейлы, отпечаток;
- `show NAME` — полная замороженная спецификация конфигурации (JSON);
- `freeze [--dir configs] [--replace]` — заморозить набор по хешу (lock);
- `verify [--dir configs] [--no-code]` — сверить замороженные файлы с lock и текущим кодом;
  код возврата 1 при дрейфе;
- `demo [--out FILE] [--seed N]` — все конфигурации на корпусе со скриптовой моделью агента:
  нарушения, исходы атак, завершённые контроли, реакции; проверка ожидаемых свойств.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .freeze import freeze, verify
from .registry import preregistered


def _demo(out: str | None, seed: int) -> int:
    from darp_range.agent import make_agent_model
    from darp_range.agent_demo.harness import new_shop

    from .build import config_seed
    from .demo import run_corpus, summarize

    cs = preregistered()
    res, states = {}, {}
    with tempfile.TemporaryDirectory(prefix="darp-configs-") as wd:
        for cfg in cs:
            cs_seed = config_seed(seed, cfg)
            shop = new_shop(Path(wd) / cfg.name, seed=cs_seed)
            model = None if cfg.defender == "simple" else make_agent_model(shop.log, mode="stub",
                                                                           seed=cs_seed)
            runs = run_corpus(shop, cfg, model)
            res[cfg.name] = dict(summarize(runs), role=cfg.role, family=cfg.family,
                                 seed=cs_seed, fingerprint=cfg.fingerprint())
            states[cfg.name] = {i: r.final_state_hash for i, r in runs.items()}
    g0v = set(res["g0"]["violations"])
    checks = {
        "g0 has executed violations (agent is vulnerable without extra defense)": bool(g0v),
        "g_proc: fewer violations than g0": len(res["g_proc"]["violations"]) < len(g0v),
        "g_proc: process rules decided at least once": res["g_proc"]["process_decisions"] > 0,
        "g_simple: no LLM tokens": not res["g_simple"]["tokens"],
        "detect_only: same DB end states as g0": states["g_in_detect_only"] == states["g0"],
        "detect_only: guard signals logged": res["g_in_detect_only"]["guard_events"] > 0,
        "detect_only: no executed reaction": res["g_in_detect_only"]["policy_non_continue"] == 0,
        "g_in reacts and lowers violations": (res["g_in"]["policy_non_continue"] > 0 and
                                              len(res["g_in"]["violations"]) < len(g0v)),
        "g0_seed2 runs on another seed (spread control)": (
            res["g0_seed2"]["attacks"] == res["g0"]["attacks"]
            and res["g0_seed2"]["seed"] != res["g0"]["seed"]),
    }
    report = {"seed": seed, "set_fingerprint": cs.fingerprint(), "configs": res,
              "checks": checks, "note": "scripted stub agent, development corpus: not a result"}
    for name, r in res.items():
        print(f"{name:38s} {r['role']:8s} viol={len(r['violations']):2d} "
              f"ctl_ok={r['controls_completed']:2d}/{r['controls']} "
              f"outcomes={r['attack_outcomes']} reactions={r['policy_non_continue']} "
              f"process={r['process_decisions']}")
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return 0 if all(checks.values()) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m darp_range.configs", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("name")
    f = sub.add_parser("freeze")
    f.add_argument("--dir", default="configs")
    f.add_argument("--replace", action="store_true")
    f.add_argument("--note", default="")
    v = sub.add_parser("verify")
    v.add_argument("--dir", default="configs")
    v.add_argument("--no-code", action="store_true")
    d = sub.add_parser("demo")
    d.add_argument("--out")
    d.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        cs = preregistered()
        for r in cs.summary():
            print(json.dumps(r, ensure_ascii=False))
        print(f"set_fingerprint {cs.fingerprint()}")
        return 0
    if a.cmd == "show":
        print(json.dumps(preregistered()[a.name].to_json(), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "freeze":
        lock = freeze(preregistered(), a.dir, note=a.note, replace=a.replace)
        print(f"frozen {len(lock['configs'])} configurations in {a.dir}: "
              f"set {lock['set_fingerprint']}")
        return 0
    if a.cmd == "verify":
        drift = verify(a.dir, check_code=not a.no_code)
        for x in drift:
            print(f"DRIFT {x.kind} {x.name}: {x.detail}")
        print("OK" if not drift else f"{len(drift)} drift item(s)")
        return 0 if not drift else 1
    return _demo(a.out, a.seed)


if __name__ == "__main__":
    sys.exit(main())
