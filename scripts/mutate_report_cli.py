"""Мутационная проверка report_cli: каждая мутация портит один фрагмент компонента в копии
рабочего каталога (временный каталог) и прогоняет test_report_cli.py. Ожидается, что все убиты.

    python3 scripts/mutate_report_cli.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CO = "darp_range/report_cli/contour.py"
LV = "darp_range/report_cli/live.py"
PL = "darp_range/report_cli/pipeline.py"
MUTANTS = [
    # --- контур NFR-2.5
    ("connect TCP не запрещён", CO,
     "            return orig_connect(self, address)\n        _deny(address)\n\n    def connect_ex",
     "            return orig_connect(self, address)\n        return orig_connect(self, address)\n\n"
     "    def connect_ex"),
    ("UDP sendto не перехвачен", CO, "    socket.socket.sendto = sendto\n", ""),
    ("сокеты не восстановлены", CO,
     "        socket.socket.connect, socket.socket.connect_ex = orig_connect, orig_connect_ex\n", ""),
    ("AF_UNIX тоже запрещён", CO,
     '    def connect(self, address):  # noqa: ANN001 — сигнатура socket.connect\n'
     '        if self.family == getattr(socket, "AF_UNIX", None):',
     '    def connect(self, address):  # noqa: ANN001 — сигнатура socket.connect\n'
     '        if False:'),
    ("сторонний импорт не замечен", CO, "                if top != PACKAGE and top not in stdlib:",
     "                if top != PACKAGE and top not in stdlib and False:"),
    ("облачный гвардрейл допущен", CO, '        if m["deployment"] not in ALLOWED_DEPLOYMENTS:',
     '        if False:'),
    ("плавающая версия допущена", CO,
     '        if str(m["version"]).strip().lower() in FLOATING_VERSIONS:', '        if False:'),
    ("проприетарная лицензия допущена", CO,
     '        if any(w in str(m["license"]).lower() for w in FORBIDDEN_LICENSE_WORDS):',
     '        if False:'),
    # --- живой бюджет
    ("у каждой модели свой счётчик", LV,
     "        return ChatModel(OpenAICompatibleBackend(s, transport=transport), role=ROLE, meter=meter,",
     "        return ChatModel(OpenAICompatibleBackend(s, transport=transport), role=ROLE,"
     " meter=RunMeter(meter.budget),"),
    ("wall-clock не проверяется", LV, "            if el >= self.budget.max_wall_s:",
     "            if False:"),
    ("отказы не считаются", LV,
     "            self.refusals[exc.limit_name] = self.refusals.get(exc.limit_name, 0) + 1\n", ""),
    ("g_simple считается с моделью", LV, '        if defenders[name] == "simple":',
     '        if False:'),
    ("повтор не учтён в плане эпизодов", LV,
     '        if plan.replay == "first_block" and sch.blocks:',
     '        if False and sch.blocks:'),
    ("лимит эпизодов не проверяется", LV, '    if pe["total"] > budget.max_episodes:',
     '    if False:'),
    ("режим A допущен вживую", LV, '    if plan.attack_mode != "D":', '    if False:'),
    ("seed модели не переопределяется", LV,
     "        s = settings if seed == settings.seed else replace(settings, seed=seed)",
     "        s = settings"),
    # --- конвейер и отчёт NFR-2.14
    ("офлайн без запрета сети", PL,
     "    with no_network() as net:\n        for s in seeds:\n"
     "            runs[s] = _run_seed(plan, s, out, names, l_viol)",
     "    from .contour import NetworkGuard\n    net = NetworkGuard()\n    if True:\n"
     "        for s in seeds:\n            runs[s] = _run_seed(plan, s, out, names, l_viol)"),
    ("дефекты не валят самопроверку", PL, "                       all(v == 0 for v in defects.values())))",
     "                       True))"),
    ("статья не помечена вне рамок", PL, '            "5_scientific_article": {"status": "out_of_scope",',
     '            "5_scientific_article": {"status": "present",'),
    ("незакрытый журнал не замечен", PL, "            if not lg.complete:", "            if False:"),
    ("verify не сверяет L*", PL,
     '        if a is None or (a["status"], a["L_star"]) != (r["status"], r["L_star"]):',
     '        if a is None or a["status"] != r["status"]:'),
    ("verify не сверяет код", PL, '    if code_now != idx["items"]["1_source_code"]["sha256"]:',
     '    if False:'),
    ("summary: L* low = base", PL,
     '                   "L_star": be["L_star"], "L_star_low_prices": be["bounds"]["low"]["L_star"],',
     '                   "L_star": be["L_star"], "L_star_low_prices": be["L_star"],'),
    ("занятый каталог перезаписывается", PL,
     "    if out.exists() and any(out.iterdir()):\n        raise PipelineError(f\"каталог {out} не пуст: результаты не перезаписываются\")",
     "    if False:\n        pass"),
    ("g0 не обязателен", PL, "    if REFERENCE not in sel:", "    if False:"),
    ("контур живого режима без endpoint", PL,
     '    if endpoint is not None:\n        # только адрес и закреплённое имя; где исполняется модель — `model_execution` ниже\n        checks["endpoint_address_local_and_model_pinned"] = bool(endpoint.get("ok"))',
     '    if False:\n        pass'),
]


def main(argv: list[str]) -> int:
    src = Path(argv[1] if len(argv) > 1 else ".").resolve()
    survivors = []
    with tempfile.TemporaryDirectory(prefix="darp-mut-report-cli-") as tmp:
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
                                ".", "-p", "test_report_cli.py"], cwd=dst,
                               capture_output=True, text=True, timeout=900)
            killed = p.returncode != 0
            print(("KILLED" if killed else "SURVIVED"), name, flush=True)
            if not killed:
                survivors.append(name)
    print(f"{len(MUTANTS) - len(survivors)}/{len(MUTANTS)} мутаций убито")
    return 0 if not survivors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
