"""Мутационная проверка configs: каждая мутация меняет один фрагмент в копии рабочего каталога
во временном каталоге и прогоняет test_configs.py. Ожидается, что все убиты.

    python3 scripts/mutate_configs.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SP = "darp_range/configs/spec.py"
RG = "darp_range/configs/registry.py"
FZ = "darp_range/configs/freeze.py"
PR = "darp_range/configs/process.py"
BD = "darp_range/configs/build.py"
MUTANTS = [
    ("исполнитель настраивается конфигурацией (схема открыта)", SP,
     "        extra = set(spec) - set(_INPUT_FIELDS) - set(_DERIVED_FIELDS)\n        if extra:",
     "        extra = set(spec) - set(_INPUT_FIELDS) - set(_DERIVED_FIELDS)\n        if False:"),
    ("граница исполнителя не сверяется", SP,
     "        if eb != BOUNDARY_FINGERPRINT:", "        if False:"),
    ("g0 допускает гвардрейлы", SP,
     "            if self.guards:\n                raise ConfigError(f\"{f}: базовая линия без гвардрейлов (NFR-2.12)\")",
     "            if False:\n                raise ConfigError(f\"{f}: базовая линия без гвардрейлов (NFR-2.12)\")"),
    ("g_proc без правил процесса", SP,
     '        if f == "g_proc" and self.process is None:', '        if False:'),
    ("правило реакции без гвардрейла допускается", SP,
     "        if orphan:", "        if False:"),
    ("detect_only допускается в основной роли", SP,
     '        if self.policy.detect_only and r not in ("control", "post_hoc"):', "        if False:"),
    ("вычисленные поля не сверяются", SP,
     "                if k in spec and spec[k] != cur[k]:", "                if False:"),
    ("пороги политики не входят в отпечаток", SP,
     '"policy": self.policy.to_json(),', '"policy": {"name": self.policy.name},'),
    ("параметры плагинов не входят в отпечаток", SP,
     '        core["resolved"] = self.resolved()',
     '        core["resolved"] = {k: v for k, v in self.resolved().items() if k == "policy_version"}'),
    ("набор без проверки повтора g0", RG,
     '            p.append("нет контроля: повтор g0 на другом seed (§5)")', "            pass"),
    ("набор без проверки чистого детектора", RG,
     "            if c.policy.detect_only and not same_detector(c, tgt):", "            if False:"),
    ("чистый детектор с реакцией", RG,
     "                      cfg.guards, cfg.policy.as_detect_only(), cfg.process, cfg.queue_discipline,",
     "                      cfg.guards, cfg.policy, cfg.process, cfg.queue_discipline,"),
    ("leave-one-out не удаляет плагин", RG,
     "        rest = spec[:i] + spec[i + 1:]", "        rest = spec"),
    ("g_proc без справедливой очереди", RG,
     '                      process=process or ProcessSpec(), queue_discipline="fair_share",',
     '                      process=process or ProcessSpec(), queue_discipline="fifo",'),
    ("верификация не сверяет sha256 файла", FZ,
     '        if sha256_file(p) != ent["sha256"]:', "        if False:"),
    ("верификация не видит лишних файлов", FZ,
     "        if p.stem not in listed:", "        if False:"),
    ("дрейф кода защиты не проверяется", FZ,
     "            if cur.get(k) != v:", "            if False:"),
    ("повторная заморозка без replace разрешена", FZ,
     "        if not replace:\n            raise FrozenConfigError",
     "        if False:\n            raise FrozenConfigError"),
    ("post_hoc с основной ролью", FZ,
     '    if cfg.role != "post_hoc":\n        raise ConfigError("после блокировки',
     '    if False:\n        raise ConfigError("после блокировки'),
    ("FreezeGuard не сверяет отпечаток", FZ,
     "        if cfg.fingerprint() != exp:", "        if False:"),
    ("FreezeGuard не сверяет собранную защиту", FZ,
     "                if rt.get(k) != v:", "                if False:"),
    ("дедупликация не работает", PR,
     "            if d == digest and 0 <= t_min - t < s.dedup_window_min:",
     "            if False:"),
    ("квота считает и отклонённые", PR,
     "        hist.append((float(t_min), digest))\n        return Admission(\"admitted\"",
     "        return Admission(\"admitted\""),
    ("квота на единицу больше", PR,
     "        if recent >= s.quota_per_window:", "        if recent > s.quota_per_window:"),
    ("R1 окно не проверяется", PR,
     "        if o is not None and (o - d).days > tp.window_days:", "        if False:"),
    ("R2 исключение для любой причины", PR,
     '    if "R2" in checks and final_sale and reason != tp.final_sale_exception:',
     '    if False:'),
    ("R5 повтор по заказу не эскалируется", PR,
     "        if other:", "        if False:"),
    ("лимит уточнений на единицу больше", PR,
     "            if self.asks >= self.spec.max_clarifications:",
     "            if self.asks > self.spec.max_clarifications:"),
    ("лимит раундов не применяется", PR,
     "        if self.rounds > spec.max_rounds:", "        if False:"),
    ("бюджет токенов не применяется", PR,
     "            if used >= spec.max_agent_tokens:", "            if False:"),
    ("решение процесса не исполняется кодом", PR,
     '        res = self._ex.execute("process", op, **args)',
     '        res = type("R", (), {"status": "ok", "code": None})()'),
    ("g_proc собирается без ворот процесса", BD,
     "            interceptor = gate\n", "            pass\n"),
    ("ведомость потока не сверяется со спецификацией", BD,
     "            elif ledger.spec.fingerprint() != cfg.process.fingerprint():",
     "            elif False:"),
    ("повтор g0 на том же seed", BD,
     '    if cfg.seed_label == "main":', "    if True:"),
]


def main(argv: list[str]) -> int:
    src = Path(argv[1] if len(argv) > 1 else ".").resolve()
    survivors = []
    with tempfile.TemporaryDirectory(prefix="darp-mut-configs-") as tmp:
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
                                ".", "-p", "test_configs.py"], cwd=dst,
                               capture_output=True, text=True, timeout=900)
            killed = p.returncode != 0
            print(("KILLED" if killed else "SURVIVED"), name, flush=True)
            if not killed:
                survivors.append(name)
    print(f"{len(MUTANTS) - len(survivors)}/{len(MUTANTS)} мутаций убито")
    return 0 if not survivors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
