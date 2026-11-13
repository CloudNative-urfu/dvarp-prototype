"""Мутационная проверка runner: каждая мутация меняет один фрагмент в копии рабочего каталога
во временном каталоге и прогоняет test_runner.py. Ожидается, что все убиты.

    python3 scripts/mutate_runner.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PL = "darp_range/runner/plan.py"
FL = "darp_range/runner/flow.py"
RP = "darp_range/runner/replay.py"
OR = "darp_range/runner/orchestrator.py"
MUTANTS = [
    ("легальные слоты заполняются атаками", PL,
     "                eid = ctl_perm[s.index % len(ctl_perm)]",
     "                eid = att_perm[s.index % len(att_perm)]"),
    ("один блок вместо покрытия всех атак", PL,
     "    blocks = plan.blocks or (math.ceil(len(attacks) / per_block) if attacks else 1)",
     "    blocks = plan.blocks or 1"),
    ("базовый вариант содержит атаки", PL,
     '            return tuple(s for s in self.slots if s.traffic == "legal")',
     "            return self.slots"),
    ("восстановление не сверяется со снимком", FL,
     "        ok = self.restored == ctx.mgr.baseline_hash", "        ok = True"),
    ("вариант потока пишется в события эпизода", FL,
     "                          attempt=attempt, t_arrival=t_arrival, config=ctx.cfg.name)",
     "                          attempt=attempt, t_arrival=t_arrival, config=ctx.cfg.name,\n"
     "                          variant=ctx.variant)"),
    ("легальный эпизод оценивается как контроль", FL,
     '                                             kind="legal" if traffic == "legal" else None)',
     "                                             kind=None)"),
    ("эскалация не определяется по состоянию", FL,
     "            escalated=bool(reviews),", "            escalated=False,"),
    ("время автоматической обработки не учитывается", FL,
     "auto_minutes=rounds * ctx.plan.minutes_per_round,", "auto_minutes=0.0,"),
    ("попытки режима A приходят одновременно", FL,
     "        t = slot.t_arrival + sum(r.auto_minutes for r in done)", "        t = slot.t_arrival"),
    ("slow/burn не решаются парой", FL,
     '        if e.traffic != "attack" or not e.verdict.pending:', "        if True:"),
    ("эталонный контроль попадает в очередь", FL,
     '        return self.traffic in ("legal", "attack")', "        return True"),
    ("эскалации не журналируются в общую очередь", FL,
     "        if e.queued and e.escalated:", "        if False:"),
    ("расходы сторон эпизода перепутаны", FL,
     '        e.costs = {"defender": c.get("defender", {}), "attacker": c.get("attacker", {}),',
     '        e.costs = {"defender": c.get("attacker", {}), "attacker": c.get("defender", {}),'),
    ("сторонний расход атакующего не проверяется", FL,
     '        "attacker_events_only_in_attack_episodes": not stray,',
     '        "attacker_events_only_in_attack_episodes": True,'),
    ("расход атакующего в базовом потоке не проверяется", FL,
     '        "base_flow_has_no_attacker_cost": variant != "base" or (',
     '        "base_flow_has_no_attacker_cost": True or ('),
    ("пара не сверяет траектории легальных эпизодов", FL,
     "        d = compare_trajectories(b.trajectory, la[k].trajectory, episode_id=k)",
     "        d = None"),
    ("пара допускает атаки в базовом потоке", FL,
     '    if any(e.traffic == "attack" for e in base.episodes):', "    if False:"),
    ("пара допускает разные конфигурации и снимки", FL,
     "    if (base.config_fingerprint, base.seed, base.block, base.baseline_hash) != \\",
     "    if (attacked.config_fingerprint, attacked.seed, attacked.block, attacked.baseline_hash) != \\"),
    ("повтор не сверяет траектории", RP,
     "        d = compare_trajectories(a.trajectory, b.trajectory, episode_id=a.episode_id)",
     "        d = None"),
    ("дефекты не пишутся в журнал прогона", OR,
     '            rw.emit("reproducibility_defect", d)', "            pass"),
    ("seed конфигурации не учитывается", OR,
     "        seed = config_seed(self.plan.master_seed, cfg)\n        sched, mgr",
     "        seed = self.plan.master_seed\n        sched, mgr"),
    ("FreezeGuard не применяется к эпизодам", OR,
     "                           model_factory=self.model_factory, freeze_guard=self.freeze_guard,",
     "                           model_factory=self.model_factory, freeze_guard=None,"),
    ("дрейф кода защиты допускается молча", OR,
     "            configs = load_frozen(self.configs_dir, allow_code_drift=allow_code_drift)",
     "            configs = load_frozen(self.configs_dir, allow_code_drift=True)"),
    ("каталог прогона перезаписывается", OR,
     "        if self.out.exists() and any(self.out.iterdir()):", "        if False:"),
]


def main(argv: list[str]) -> int:
    src = Path(argv[1] if len(argv) > 1 else ".").resolve()
    survivors = []
    with tempfile.TemporaryDirectory(prefix="darp-mut-runner-") as tmp:
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
                                ".", "-p", "test_runner.py"], cwd=dst,
                               capture_output=True, text=True, timeout=900)
            killed = p.returncode != 0
            print(("KILLED" if killed else "SURVIVED"), name, flush=True)
            if not killed:
                survivors.append(name)
    print(f"{len(MUTANTS) - len(survivors)}/{len(MUTANTS)} мутаций убито")
    return 0 if not survivors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
