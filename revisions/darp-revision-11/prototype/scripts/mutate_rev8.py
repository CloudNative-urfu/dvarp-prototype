"""Ревизия 8: мутационные проверки одобренных задач (одна строка на мутацию, копия пакета во временном
каталоге). Ожидается, что каждый мутант убит (RED) тестами своей группы.

    python3 scripts/mutate_rev8.py [--group append_only|neutrality|order_scope|patient|live_labels|all] [--log FILE]

Группы:
- append_only: без `recursive_triggers` (REPLACE снова переписывает строку); PRAGMA с аргументом
  разрешена; ATTACH разрешён; TEMP-триггеры не ставятся; любая прагма разрешена;
- neutrality: basis / код отказа / сумма в agent_view маршрутизированного результата (мутант критика
  rev7, который пропускал тест по подстроке «20000»);
- order_scope: диагностика считает любую маршрутизацию «в заказе обращения»; сравнивает заказ позиции
  с самим собой;
- patient: facts_patient не подталкивает (= facts); бесконечное терпение; подталкивание со словом
  согласия; подталкивание question_only; facts_patient отвечает «yes» на просьбу подтвердить;
- live_labels: requires_manual_review не b_rule; invalid_argument не b_args; agent_stalled →
  a_customer_left; дефект пары не фиксируется, если запрос разошёлся раньше ответа исполнителя;
  фабрика модели на эпизод (одинаковые case id пары делят лимит L); допуск пары без бюджета прохода.
Код 0 — все убиты, 1 — иначе.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EX = "darp_range/executor/executor.py"
LU = "darp_range/channel/legal_user.py"
OS = "scripts/rev8_order_scope_diag.py"
SL = "scripts/rev8_live_legal_slice.py"

GROUPS: dict[str, dict] = {
    "append_only": {
        "tests": ["tests.test_revision8_append_only"],
        "mutants": [
            ("без recursive_triggers (REPLACE переписывает строку)", EX,
             '    conn.execute("PRAGMA recursive_triggers = ON")\n', ""),
            ("PRAGMA с аргументом разрешена", EX,
             "    return name in READ_PRAGMAS and (arg is None or name in READ_PRAGMAS_WITH_ARG)",
             "    return name in READ_PRAGMAS or arg is not None"),
            ("любая прагма разрешена", EX,
             "    if action == sqlite3.SQLITE_PRAGMA and not _read_pragma(arg1, arg2):",
             "    if False:"),
            ("ATTACH разрешён", EX,
             "    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):",
             "    if action == sqlite3.SQLITE_DETACH:"),
            ("TEMP-триггеры не ставятся", EX,
             "            _install_append_only_triggers(self._conn)   # до авторизатора: он запрещает DDL\n",
             ""),
        ],
    },
    "neutrality": {
        "tests": ["tests.test_revision8_routing_neutrality"],
        "mutants": [
            ("basis в agent_view маршрутизации", EX,
             '"result": {"escalated": True}}',
             '"result": {"escalated": True, "basis": self.basis}}'),
            ("код/причина маршрутизации в agent_view", EX,
             '"result": {"escalated": True}}',
             '"result": {"escalated": True, "reason_code": self.routed}}'),
            ("сумма в agent_view маршрутизации", EX,
             'return {"status": "escalated", "reason": ROUTED_MESSAGE,',
             'return {"status": "escalated", "reason": ROUTED_MESSAGE + f" {self.data}",'),
        ],
    },
    "order_scope": {
        "tests": ["tests.test_revision8_routing_neutrality"],
        "mutants": [
            ("любая маршрутизация «в заказе обращения»", OS,
             '"in_order" if t_order == i_order else "out_of_order")',
             '"in_order")'),
            ("заказ позиции не читается (сравнение с заказом обращения)", OS,
             '    t_order, i_order = (t[0] if t else None), (i[0] if i else None)',
             '    t_order = i_order = (t[0] if t else None)'),
        ],
    },
    "patient": {
        "tests": ["tests.test_revision8_patient_customer"],
        "mutants": [
            ("facts_patient не подталкивает (= facts)", LU,
             "        if not asked and self.confirm_mode == FACTS_PATIENT:\n",
             "        if False:\n"),
            ("бесконечное терпение", LU,
             "        if self._nudges < self.max_nudges:\n", "        if True:\n"),
            ("подталкивание со словом согласия", LU,
             'STATUS_NUDGES = ("is it done? what happens now with my request",',
             'STATUS_NUDGES = ("ok, is it done? what happens now with my request",'),
            ("question_only подталкивается", LU,
             '            self.end_reason = "no_question"\n            return None\n'
             '        # Ревизия 3 (K3.1)',
             '            return self._nudge_or_stall() if self.confirm_mode == FACTS_PATIENT '
             'else None\n        # Ревизия 3 (K3.1)'),
            ("facts_patient отвечает yes на подтверждение", LU,
             "        reply = facts_reply(self.facts, other)\n",
             '        reply = "yes, " + facts_reply(self.facts, other)\n'),
            ("уход без подталкивания помечается no_question", LU,
             '        self.end_reason = "agent_stalled"\n', '        self.end_reason = "no_question"\n'),
        ],
    },
    "live_labels": {
        "tests": ["tests.test_revision8_live_slice"],
        "mutants": [
            ("requires_manual_review не правило (уходит из b_rule)", SL,
             '"final_sale_not_refundable",\n              "requires_manual_review")',
             '"final_sale_not_refundable")'),
            ("invalid_argument не аргументная ошибка (уходит из b_args)", SL,
             'ARGS_CODES = ("invalid_argument", ', 'ARGS_CODES = ('),
            ("agent_stalled → a_customer_left", SL,
             '        cat = "a_stalled" if user_end == "agent_stalled" else "a_customer_left"',
             '        cat = "a_customer_left"'),
            ("любое расхождение пары считается расхождением ответа исполнителя", SL,
             '                if role == "tool" and y.get("role") == "tool":',
             '                if True:'),
            ("фабрика модели на эпизод (пара делит лимит L)", SL,
             "                    factory = live_factory\n",
             "                    factory = live_model_factory(settings, meter, transport=capture)\n"),
            ("id tool_call провайдера не нормализуются (D8.7)", SL,
             "    ra, rb = [normalize_ids(x) for x in ra], [normalize_ids(x) for x in rb]\n", ""),
            ("допуск пары без учёта бюджета прохода", SL,
             "            admitted = (min(run_left, pass_left) >= 2 * L",
             "            admitted = (run_left >= 2 * L"),
        ],
    },
}


def run_group(root: Path, name: str) -> tuple[int, list[dict]]:
    g = GROUPS[name]
    rows, survived = [], 0
    for title, rel, old, new in g["mutants"]:
        with tempfile.TemporaryDirectory() as td:
            for sub in ("darp_range", "tests", "configs", "corpus", "scripts"):
                if (root / sub).exists():
                    shutil.copytree(root / sub, Path(td) / sub,
                                    ignore=shutil.ignore_patterns("__pycache__"))
            (Path(td) / "artifacts").mkdir()
            for f in root.glob("artifacts/preregistration-rev*"):
                shutil.copy2(f, Path(td) / "artifacts" / f.name)
            live7 = root / "artifacts/checks/revision7/live_legal"
            if live7.is_dir():
                shutil.copytree(live7, Path(td) / "artifacts/checks/revision7/live_legal")
            p = Path(td) / rel
            src = p.read_text(encoding="utf-8")
            if old not in src:
                rows.append({"group": name, "mutant": title, "status": "BROKEN (нет строки)"})
                survived += 1
                print(f"BROKEN MUTANT: {name}: {title}", flush=True)
                continue
            p.write_text(src.replace(old, new, 1), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "unittest", *g["tests"]], cwd=td,
                               capture_output=True, text=True, timeout=1800)
            killed = r.returncode != 0
            survived += not killed
            tail = [ln for ln in r.stderr.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
            status = "killed" if killed else "SURVIVED"
            rows.append({"group": name, "mutant": title, "file": rel, "status": status,
                         "failing_tests": tail[:8]})
            print(f"{status:10s}{name}: {title}", flush=True)
    return survived, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="all", choices=[*GROUPS, "all"])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--log", type=Path)
    a = ap.parse_args(argv)
    names = list(GROUPS) if a.group == "all" else [a.group]
    total, rows = 0, []
    for n in names:
        s, r = run_group(a.root.resolve(), n)
        total += s
        rows += r
    print(f"итог: убито {len(rows) - total}/{len(rows)}")
    if a.log:
        a.log.parent.mkdir(parents=True, exist_ok=True)
        a.log.write_text(json.dumps({"mutants": len(rows), "survived": total, "ok": total == 0,
                                     "rows": rows}, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
