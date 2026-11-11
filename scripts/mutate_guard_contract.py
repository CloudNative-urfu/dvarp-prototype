"""Мутационная проверка guard_contract: каждая мутация меняет одну строку в копии пакета во
временном каталоге и прогоняет test_guard_contract.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_guard_contract.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TY = "darp_range/guard_contract/types.py"
PL = "darp_range/guard_contract/plugin.py"
RG = "darp_range/guard_contract/registry.py"
HO = "darp_range/guard_contract/host.py"
CX = "darp_range/guard_contract/context.py"
MUTANTS = [
    # FR-1.8: запись в БД
    ("авторизатор разрешает всё", PL, "    return sqlite3.SQLITE_DENY\n", "    return sqlite3.SQLITE_OK\n"),
    ("БД плагина без авторизатора", PL, "    conn.set_authorizer(_authorizer)\n", ""),
    ("нет проверки data_version", HO, "        if self._db_version() != v0:\n            failure =",
     "        if False:\n            failure ="),
    ("запись в обход проглатывается", HO, "        if isinstance(failure, GuardContractViolation):\n            # дефект",
     "        if False:\n            # дефект"),
    ("решение вместо сигнала разрешено", TY, "        if forbidden:\n            raise GuardContractViolation(",
     "        if False:\n            raise GuardContractViolation("),
    ("не-сигнал принимается", TY, '    raise GuardContractViolation(\n        f"плагин вернул {type(value).__name__}',
     '    return GuardSignal("allow")\n    raise GuardContractViolation(\n        f"плагин вернул {type(value).__name__}'),
    ("вход не заморожен (args)", TY, '        object.__setattr__(self, "args", freeze(self.args, "ProposedCall.args"))',
     '        object.__setattr__(self, "args", dict(self.args))'),
    ("вложенные значения не заморожены", TY, "        return MappingProxyType(out)", "        return out"),
    ("параметры плагина не заморожены", PL, '        object.__setattr__(self, "_frozen", True)', "        pass"),
    # FR-1.6: вход
    ("политика считается недоверенной", TY, 'TRUSTED_PROVENANCES = frozenset({"trusted_policy", "system"})',
     'TRUSTED_PROVENANCES = frozenset({"system"})'),
    ("данные заказа доверенные", TY, 'TRUSTED_PROVENANCES = frozenset({"trusted_policy", "system"})',
     'TRUSTED_PROVENANCES = frozenset({"trusted_policy", "system", "order_data"})'),
    ("происхождение не проверяется", TY, "        if self.provenance not in PROVENANCES:", "        if False:"),
    ("tool без вызова", TY, '        if self.stage == "tool" and self.proposed_call is None:', "        if False:"),
    ("output без черновика", TY, '        if self.stage == "output" and self.candidate_output is None:', "        if False:"),
    ("контекст истории теряет источник", CX, 'f"ticket:{tid}:msg:{m[\'seq\']}"', 'f"ticket:{tid}"'),
    ("контекст заказа теряет происхождение", CX, 'ContextFragment(c["text"], c["provenance"], f"order:',
     'ContextFragment(c["text"], "system", f"order:'),
    # FR-1.7: сигнал и затраты
    ("score без значения", TY, '        elif self.signal == "score":\n            raise', '        elif False:\n            raise'),
    ("score вне [0,1]", TY, "or not 0.0 <= s <= 1.0:\n                raise GuardContractError(f\"GuardSignal.score",
     "or not 0.0 <= s <= 9.0:\n                raise GuardContractError(f\"GuardSignal.score"),
    ("targets не сверяются", HO, "            sig.check_targets(inp)\n", ""),
    ("начисления плагина теряются", HO, "        cost = dict(rt.charged, latency_ms=", "        cost = dict(latency_ms="),
    ("сбой без записи затрат", HO, '        writer.emit("guard_error", data,', '        (lambda *a, **k: None)("guard_error", data,'),
    ("плагин подделывает время", PL, 'k in ("latency_ms", "cpu_ms", "manual_minutes")', 'k in ("manual_minutes",)'),
    ("модель не начисляет токены", PL, '        self.charge(tokens_in=c["tokens_in"], tokens_out=c["tokens_out"], requests=c["requests"])',
     "        pass"),
    ("текст входа в журнале", HO, '        "text_chars": len(inp.text),', '        "text_chars": inp.text,'),
    # FR-1.5: реестр и набор
    ("дубль id разрешён", RG, "        if prev is not None and prev is not cls:", "        if False:"),
    ("закрытый API допускается", PL, 'DEPLOYMENTS = ("builtin", "self_hosted")', 'DEPLOYMENTS = ("builtin", "self_hosted", "commercial_api")'),
    ("выключенный адаптер создаётся", RG, "        if not cls.enabled_by_default and not allow_disabled:", "        if False:"),
    ("неподдерживаемая точка установки", RG, "            if bad:\n                raise GuardContractError(\n                    f\"{pid}: точки",
     "            if False:\n                raise GuardContractError(\n                    f\"{pid}: точки"),
    ("лишние поля спецификации", RG, "            if extra:\n                raise GuardContractError(f\"spec[{i}]: неизвестные",
     "            if False:\n                raise GuardContractError(f\"spec[{i}]: неизвестные"),
    ("отпечаток без параметров", PL, '                "params": thaw(self.params)}', '                "params": {}}'),
    ("неизвестные параметры разрешены", PL, "        if unknown:\n            raise GuardContractError(f\"{cls.plugin_id}: неизвестные",
     "        if False:\n            raise GuardContractError(f\"{cls.plugin_id}: неизвестные"),
    ("стадия не проверяется хостом", HO, "        if inp.stage not in plugin.stages:", "        if False:"),
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
            r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".",
                                "-p", "test_guard_contract.py"], cwd=td, capture_output=True)
            ok = r.returncode != 0
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
