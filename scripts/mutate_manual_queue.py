"""Мутационная проверка manual_queue: каждая мутация меняет одну строку в копии пакета во
временном каталоге и прогоняет test_manual_queue.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_manual_queue.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MQ = "darp_range/manual_queue/"
MUTANTS = [
    ("unbounded capacity (operator never busy)", MQ + "model.py",
     "op = heapq.heappop(free)", "op = free[0]"),
    ("operator not freed", MQ + "model.py",
     "heapq.heappush(free, op_of[i])", "pass"),
    ("service time ignored", MQ + "model.py",
     "heapq.heappush(heap, (t + service[j], _DEPART, j))", "heapq.heappush(heap, (t, _DEPART, j))"),
    ("arrival before departure on tie", MQ + "model.py",
     "_DEPART, _ARRIVE = 0, 1", "_DEPART, _ARRIVE = 1, 0"),
    ("lifo instead of fifo", MQ + "model.py", "return self.fifo.popleft()", "return self.fifo.pop()"),
    ("fair_share degenerates to fifo", MQ + "model.py",
     "key=lambda a: (self.served.get(a, 0), self.by_account[a][0]))",
     "key=lambda a: self.by_account[a][0])"),
    ("enqueue ignores auto processing", MQ + "model.py",
     "return self.t_arrival + self.auto_minutes", "return self.t_arrival"),
    ("delay excludes waiting", MQ + "model.py",
     "delay = end[i] - c.t_arrival", "delay = end[i] - ts + c.auto_minutes"),
    ("separate queue per traffic", MQ + "model.py",
     "waiting.push(i, esc[i].account)", "waiting.push(i, esc[i].account) if esc[i].traffic != 'attack' else None"),
    ("depth before dispatch", MQ + "model.py",
     "        timeline.append((t, waiting.n))\n", "        timeline.append((t, waiting.n + 1))\n"),
    ("exp service shared stream", MQ + "model.py",
     'rng = make_rng(cfg.seed, "manual_queue", "service", case.case_id)',
     'rng = make_rng(cfg.seed, "manual_queue", "service")'),
    ("E over escalated only", MQ + "model.py",
     '"E": escalated / total if total else 0.0', '"E": 1.0 if escalated else 0.0'),
    ("no manual_minutes cost", MQ + "model.py",
     'cost={"manual_minutes": o.service_minutes}', 'cost=None'),
    ("enqueue not logged", MQ + "model.py",
     "if case.escalated and self._writer is not None:", "if False:"),
    ("legal flow depends on attacks", MQ + "arrivals.py",
     'legal = poisson_times(seed, "legal", n_legal, mean_gap)',
     'legal = poisson_times(seed, f"legal{w_a if with_attacks else 0.0}", n_legal, mean_gap)'),
    ("attack share ignored", MQ + "arrivals.py",
     "return round(n_legal * w_a / (1.0 - w_a))", "return round(n_legal * w_a)"),
    ("attacks not in lambda_Q", MQ + "analytic.py",
     'lam_q = a["lambda_L_per_min"] * e_legal + a["lambda_A_per_min"] * e_attack',
     'lam_q = a["lambda_L_per_min"] * e_legal'),
    ("conditional wait in Q_L", MQ + "analytic.py",
     '"Q_L": e_legal * c["W_q"] if ok else None', '"Q_L": c["W_q"] if ok else None'),
    ("erlang C as erlang B", MQ + "analytic.py",
     "return b / (1.0 - load / servers * (1.0 - b))", "return b"),
    ("sla tail without decay", MQ + "analytic.py",
     '"P_wq_gt_sla": pw * math.exp(-gap * sla_minutes)}', '"P_wq_gt_sla": pw}'),
    ("unstable queue reported stable", MQ + "analytic.py",
     '"stable": rho < 1.0}', '"stable": True}'),
    ("sla ignored", MQ + "model.py", "delay > config.sla_minutes)", "False)"),
    ("utilization ignores operators", MQ + "model.py",
     "return busy / (self.config.operators * (t1 - t0))", "return busy / (t1 - t0)"),
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
                                    "-p", "test_manual_queue.py"], cwd=td, capture_output=True, timeout=120)
                ok = r.returncode != 0
            except subprocess.TimeoutExpired:
                ok = True  # зависание тоже обнаружено
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
