"""Мутационная проверка metrics: каждая мутация портит один фрагмент формул в копии рабочего
каталога (временный каталог) и прогоняет test_metrics.py. Ожидается, что все убиты.

    python3 scripts/mutate_metrics.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BE = "darp_range/metrics/breakeven.py"
EX = "darp_range/metrics/extract.py"
FR = "darp_range/metrics/frontier.py"
AN = "darp_range/metrics/analysis.py"
ST = "darp_range/metrics/stats.py"
MUTANTS = [
    # --- формула ΔNet / L* (§9.1–9.2)
    ("знак ΔV перевёрнут", BE, '        "dV": ref.V - g.V,', '        "dV": g.V - ref.V,'),
    ("ΔR не взвешен w_L", BE, '        "refuse": w_l * (g.R - ref.R) * p.C_refuse,',
     '        "refuse": (g.R - ref.R) * p.C_refuse,'),
    ("ΔE взвешен w_L", BE, '        "manual": (g.E - ref.E) * p.C_manual,',
     '        "manual": w_l * (g.E - ref.E) * p.C_manual,'),
    ("ΔC_ep не взвешен w_A", BE, '        "attack_service": w_a * (g.C_ep - ref.C_ep),',
     '        "attack_service": (g.C_ep - ref.C_ep),'),
    ("C_guard выпал из числителя", BE,
     '    return math.fsum((t["refuse"], t["queue"], t["attack_service"], t["guard"], t["manual"]))',
     '    return math.fsum((t["refuse"], t["queue"], t["attack_service"], t["manual"]))'),
    ("очередь выпала из числителя", BE,
     '    return math.fsum((t["refuse"], t["queue"], t["attack_service"], t["guard"], t["manual"]))',
     '    return math.fsum((t["refuse"], t["attack_service"], t["guard"], t["manual"]))'),
    ("L* без w_A в знаменателе", BE, "        ls = num / (t[\"w_A\"] * dv)", "        ls = num / dv"),
    ("ΔV = 0 трактуется как порог", BE, "    elif abs(dv) <= EPS:", "    elif False:"),
    ("ΔV < 0 трактуется как порог", BE, "    elif dv < 0:", "    elif False and dv < 0:"),
    ("окупается при L_viol = L*", BE, '    return l_viol > res["L_star"]', '    return l_viol >= res["L_star"]'),
    ("ΔNet без цены нарушения", BE, '    return t["w_A"] * t["dV"] * l_viol - numerator(t)',
     '    return t["w_A"] * t["dV"] - numerator(t)'),
    ("углы без ставок вычислений", BE,
     '        lo_hi("C_refuse"), lo_hi("C_queue"), lo_hi("C_manual"), ("low", "high"))]',
     '        lo_hi("C_refuse"), lo_hi("C_queue"), lo_hi("C_manual"), ("base",))]'),
    ("устойчивость не учитывает углы", BE, '        "verdict_robust": len(sts) == 1,',
     '        "verdict_robust": True,'),
    ("цены границ перепутаны", BE,
     '    return {"low": Prices(**lo, rates="low"), "base": Prices(), "high": Prices(**hi, rates="high")}',
     '    return {"low": Prices(**hi, rates="low"), "base": Prices(), "high": Prices(**lo, rates="high")}'),
    ("Q в режимах очереди не подставляется", BE, '        g = Inputs(**{**asdict(g_inp), "Q": gs[mode]["Q"]})',
     "        g = g_inp"),
    # --- агрегаты (FR-1.23, NFR-2.9)
    ("self-limited входит в C_ep", EX,
     '        "C_ep": mean(e.usd(rates, exclude=[GUARD_BUCKET]) for e in att),',
     '        "C_ep": mean(e.usd(rates, exclude=[GUARD_BUCKET]) + compute_usd(e.attacker, rates) for e in att),'),
    ("C_guard считается дважды (в C_ep)", EX,
     '        "C_ep": mean(e.usd(rates, exclude=[GUARD_BUCKET]) for e in att),',
     '        "C_ep": mean(e.usd(rates) for e in att),'),
    ("C_guard только по атакам", EX, '        "C_guard": mean(e.usd(rates, [GUARD_BUCKET]) for e in all_eps),',
     '        "C_guard": mean(e.usd(rates, [GUARD_BUCKET]) for e in att),'),
    ("R по эскалациям, не отказам", EX,
     '    r = cluster_rate((e.pair_id, e.task_status == "unjustified_refusal") for e in legal)',
     '    r = cluster_rate((e.pair_id, e.task_status != "completed") for e in legal)'),
    ("E только по легальным", EX, '        "E": esc / n, "escalations": esc,',
     '        "E": sum(e.escalated for e in legal) / n, "escalations": esc,'),
    ("Q из потока без атак", EX, '        "Q": qa["Q"], "Q_base_flow": qb["Q"],',
     '        "Q": qb["Q"], "Q_base_flow": qb["Q"],'),
    ("эталонные контроли пары входят в поток", EX, '            if e["traffic"] in TRAFFIC:', "            if True:"),
    ("детекция без flag", EX, 'DETECT_SIGNALS = ("flag", "block")', 'DETECT_SIGNALS = ("block",)'),
    ("FP считаются по атакам", EX, "    fp = sum(e.detected for e in legal)", "    fp = sum(not e.detected for e in att)"),
    ("V по исходу executed без состояния", EX,
     "    viol = cluster_rate((e.pair_id, e.violation) for e in att)",
     '    viol = cluster_rate((e.pair_id, e.outcome != "ignored") for e in att)'),
    ("research-mode не сверяется", EX,
     "        if bool(att.get(\"research_mode\")) != research_mode:", "        if False:"),
    ("PPV без доли атак", EX, "    den = tpr * base_rate + fpr * (1 - base_rate)", "    den = tpr + fpr"),
    # --- фронт, leave-one-out, сборка
    ("доминирование без строгости", FR,
     '                  and (q["reduction"] > p["reduction"] + EPS or q["cost"] < p["cost"] - EPS)]',
     "                  ]"),
    ("контроли входят во фронт", FR, 'FRONT_ROLES = ("baseline", "main")',
     'FRONT_ROLES = ("baseline", "main", "control", "loo")'),
    ("loo: родитель и вариант перепутаны", FR, "        g, ref = inputs[parent], inputs[n]",
     "        g, ref = inputs[n], inputs[parent]"),
    ("чистый расход без условия роста работы гвардрейла", FR,
     "        pure = no_gain and r_with >= r_without - EPS and guard_up",
     "        pure = no_gain and r_with >= r_without - EPS"),
    ("рост работы гвардрейла по шумному C_guard", FR,
     '        guard_up = (pe["guard_calls_per_episode"] > ae["guard_calls_per_episode"] + EPS',
     '        guard_up = (cg_with > cg_without + EPS'),
    ("расход без выигрыша не распознаётся", FR,
     '        expense = no_gain and res["numerator"] > EPS and (process_up or guard_up)',
     "        expense = False"),
    ("доли потока из конфигурации, не из g0", BE, "    w_a, w_l = ref.w_A, ref.w_L", "    w_a, w_l = g.w_A, g.w_L"),
    ("избыточность не отмечается", FR, "        if len(group) < 2:", "        if True:"),
    ("общий поток не сверяется", AN, "            \"same_flow_as_reference\": (cr.schedule_sha256 == ref_cr.schedule_sha256",
     "            \"same_flow_as_reference\": (True"),
    ("McNemar на разных потоках", AN, "    if g.schedule_sha256 != ref.schedule_sha256 or g.seed != ref.seed:",
     "    if False:"),
    ("неэффективные без недоминируемости", AN, "        if n in dominated:", "        if False:"),
    # --- статистика
    ("Wilson без поправки центра", ST, "    centre = (p + z2 / (2 * n)) / den", "    centre = p"),
    ("Newcombe с неверной стороной", ST,
     "    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)",
     "    lo = d - math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)"),
    ("McNemar без точного теста", ST, "    elif disc < 25:", "    elif disc < 0:"),
    ("интервал по эпизодам, не сценариям", ST, "    ci = wilson(p_sc * n_sc, n_sc) if n_sc else None",
     "    ci = wilson(k_ep, n_ep) if n_sc else None"),
]


def main(argv: list[str]) -> int:
    src = Path(argv[1] if len(argv) > 1 else ".").resolve()
    survivors = []
    with tempfile.TemporaryDirectory(prefix="darp-mut-metrics-") as tmp:
        for name, rel, old, new in MUTANTS:
            dst = Path(tmp) / "w"
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "tmp", "runs", ".cache", "venv", ".venv", ".runtime", "artifacts",
                ".home"))
            f = dst / rel
            text = f.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"BAD   {name}: шаблон найден {text.count(old)} раз")
                survivors.append(name)
                continue
            f.write_text(text.replace(old, new), encoding="utf-8")
            p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t",
                                ".", "-p", "test_metrics.py"], cwd=dst,
                               capture_output=True, text=True, timeout=900)
            killed = p.returncode != 0
            print(("KILLED" if killed else "SURVIVED"), name, flush=True)
            if not killed:
                survivors.append(name)
    print(f"{len(MUTANTS) - len(survivors)}/{len(MUTANTS)} мутаций убито")
    return 0 if not survivors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
