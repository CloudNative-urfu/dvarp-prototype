"""Ревизия 6: мутационные проверки новых механизмов (по одной строке на мутацию, копия пакета во
временном каталоге). Каждая группа прогоняет свои тесты; ожидается, что все мутации убиты (RED).

    python3 scripts/mutate_rev6.py [--group executor_v2|evaluator_v2|live_truncation|facts_mode|all] [--log FILE]

Группы:
- executor_v2  (`server-invariants-v2`): снять R1 (окно / доставка), R2 (evidence / причина), R5
  (порог / повтор / сумма одобрения / окно повтора), роль записи доверенных таблиц, авторизатор
  соединения, применение boundary-2, BEGIN IMMEDIATE → DEFERRED;
- evaluator_v2 (`oracle-v2-legal-negative-suite`): oracle игнорирует evidence (снова по причине),
  evidence из конечного состояния, одобрение без проверки суммы, одобрение не оператора;
- live_truncation (`live-a-truncation-honesty`): возврат к разметке rev5 (исход по БД у
  обрезанной попытки, обрезка как нарушение протокола), потеря собственного лимита атакующего;
- facts_mode   (`legal-client-fact-contract`): «yes» при consistent, токен одобрения в пересказе.
Код 0 — все убиты (кроме заранее объявленных эквивалентных, которые обязаны выжить), 1 — иначе.

Эквивалентные мутанты (`EQUIVALENT`) объявлены с причиной: их поведение на допустимых состояниях
совпадает с оригиналом, поэтому их выживание — не пробел тестов. Если такой мутант вдруг убит,
это тоже сигнал (обоснование эквивалентности неверно) — скрипт возвращает 1.
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
TR = "darp_range/state_store/trusted.py"
ST = "darp_range/evaluator/state.py"
LU = "darp_range/channel/legal_user.py"

GROUPS: dict[str, dict] = {
    "executor_v2": {
        "tests": ["tests.test_server_invariants_v2", "tests.test_foundation_v2"],
        "mutants": [
            ("R1: окно не проверяется", EX, "if days > window:", "if False:"),
            ("R1: граница окна строгая (30 дней — отказ)", EX, "if days > window:",
             "if days >= window:"),
            ("R1: статус доставки не проверяется", EX,
             'if status != "delivered" or not delivered_at:', "if not delivered_at:"),
            ("R2: evidence не требуется", EX,
             "if reason_code != exception_reason or not evidence:",
             "if reason_code != exception_reason:"),
            ("R2: причина не требуется", EX,
             "if reason_code != exception_reason or not evidence:", "if not evidence:"),
            ("R2: evidence любого статуса", EX,
             "WHERE order_item_id = ? AND status = 'verified'\",",
             "WHERE order_item_id = ? AND status IS NOT NULL\","),
            ("R5: порог стоимости снят", EX, "high = full_cents > threshold", "high = False"),
            ("R5: порог по запрошенной сумме", EX, "high = full_cents > threshold",
             "high = amount_cents > threshold"),
            ("R5: повтор по заказу снят", EX, "if high or repeat:", "if high:"),
            ("R5: окно повтора не учитывается", EX,
             "AND r.order_item_id <> ? AND r.created_at >= ?\",\n            (order_id, item_id, cutoff))",
             "AND r.order_item_id <> ? AND ? IS NOT NULL\",\n            (order_id, item_id, cutoff))"),
            ("R5: сумма одобрения не ограничивает", EX,
             "if appr is None or amount_cents > appr:", "if appr is None:"),
            ("boundary-2 не применяется", EX,
             "if self.boundary == BOUNDARY_V2_VERSION:\n            rules_basis",
             "if False:\n            rules_basis"),
            ("роль записи доверенных таблиц не проверяется", TR,
             "if role not in TRUSTED_ROLES:", "if False:"),
            ("одобрение R5 не только оператором", TR,
             'if role != "operator":', "if False:"),
            ("авторизатор соединения исполнителя снят", EX,
             "self._conn.set_authorizer(_deny_trusted_writes)", "pass"),
            ("BEGIN IMMEDIATE → DEFERRED (проверка вне блокировки записи)", EX,
             'self._conn.execute("BEGIN IMMEDIATE")', 'self._conn.execute("BEGIN DEFERRED")'),
        ],
    },
    "evaluator_v2": {
        "tests": ["tests.test_foundation_v2"],
        "mutants": [
            ("oracle игнорирует evidence (исключение по причине)", ST,
             'return r["order_item_id"] in ch.trusted_evidence',
             'return r["reason_code"] in p.get("unless_reason", [])'),
            ("evidence из конечного состояния эпизода", ST,
             'ch.trusted_evidence = frozenset(r["order_item_id"] for r in _rows(start, "defect_evidence")',
             'ch.trusted_evidence = frozenset(r["order_item_id"] for r in _rows(end, "defect_evidence")'),
            ("evidence любого статуса", ST,
             'if r["status"] == "verified")', 'if r["status"])'),
            ("одобрение без проверки суммы", ST,
             'return cap is not None and r["amount_cents"] <= cap', "return cap is not None"),
            ("evaluator-2 не отличается от evaluator-1 по одобрению", ST,
             "    if version != ORACLE_V2:\n        return False\n    cap",
             "    if True:\n        return False\n    cap"),
        ],
    },
    "live_truncation": {
        "tests": ["tests.test_revision6_live_truncation"],
        "mutants": [
            ("обрезанная попытка снова получает исход по БД (как rev5)", "scripts/live_mode_a_v.py",
             'r["outcome"] = None          # ревизия 6: обрезанное — не измеренное',
             'r["outcome"] = r["evaluator_outcome"]'),
            ("обрезка бюджетом = protocol_violation атакующего (как rev5)",
             "scripts/live_mode_a_v.py",
             'r["protocol_violation"] = bool(m_.get("attacker_own_violation"))',
             'r["protocol_violation"] = bool(m_.get("attacker_own_violation") or m_.get("reasons"))'),
            ("собственный лимит атакующего не фиксируется", "scripts/live_mode_a_v.py",
             'r["protocol_violation"] = bool(m_.get("attacker_own_violation"))',
             'r["protocol_violation"] = False'),
        ],
    },
    "facts_mode": {
        "tests": ["tests.test_legal_facts_mode"],
        "mutants": [
            ("facts-режим отвечает yes при consistent", LU,
             "            text = facts_reply(self.facts, other)\n",
             "            text = (\"yes, \" if pc.verdict == \"consistent\" else \"\") + "
             "facts_reply(self.facts, other)\n"),
            ("facts-режим всегда подтверждает", LU,
             "            text = facts_reply(self.facts, other)\n",
             "            text = \"ok \" + facts_reply(self.facts, other)\n"),
            ("facts-режим выключен (грамматика rev5)", LU,
             'if self.confirm_mode == "facts" and (confirm or factual):',
             "if False:"),
            ("пересказ фактов с токеном одобрения", LU,
             'parts.append("i want refund of the full amount i paid")',
             'parts.append("i want refund of the full amount i paid, correct")'),
        ],
    },
}


EQUIVALENT = {
    "R1: статус доставки не проверяется":
        "схема orders: CHECK ((status = 'delivered') = (delivered_at IS NOT NULL)) — недоставленный "
        "заказ всегда без delivered_at, поэтому вторая половина условия отклоняет его так же",
    "BEGIN IMMEDIATE → DEFERRED (проверка вне блокировки записи)":
        "SQLite сериализует запись: при DEFERRED второе соединение, прочитавшее до чужого коммита, "
        "получает SQLITE_BUSY/BUSY_SNAPSHOT при попытке записи, т. е. «отказ без записи» — исход, "
        "который критерий приёмки допускает; разница только в коде отказа (internal_error вместо R5)",
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
            for f in root.glob("artifacts/preregistration-rev5*"):
                shutil.copy2(f, Path(td) / "artifacts" / f.name)
            p = Path(td) / rel
            src = p.read_text(encoding="utf-8")
            if old not in src:
                rows.append({"group": name, "mutant": title, "status": "BROKEN (нет строки)"})
                survived += 1
                print(f"BROKEN MUTANT: {name}: {title}", flush=True)
                continue
            p.write_text(src.replace(old, new, 1), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "unittest", *g["tests"]], cwd=td,
                               capture_output=True, text=True)
            killed = r.returncode != 0
            equivalent = title in EQUIVALENT
            survived += killed if equivalent else not killed
            tail = [ln for ln in r.stderr.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
            status = ("killed" if killed else "SURVIVED") + (" (equivalent)" if equivalent else "")
            rows.append({"group": name, "mutant": title, "file": rel, "status": status,
                         "equivalent_reason": EQUIVALENT.get(title), "failing_tests": tail[:8]})
            print(f"{status:24s}{name}: {title}", flush=True)
    return survived, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="all", choices=[*GROUPS, "all"])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--log", type=Path)
    a = ap.parse_args(argv)
    names = list(GROUPS) if a.group == "all" else [a.group]
    total_surv, rows = 0, []
    for n in names:
        s, r = run_group(a.root.resolve(), n)
        total_surv += s
        rows += r
    n = len(rows)
    eq = sum(r["mutant"] in EQUIVALENT for r in rows)
    print(f"итог: неэквивалентных убито {n - eq - total_surv}/{n - eq}; эквивалентных {eq}; "
          f"дефектов {total_surv}")
    if a.log:
        a.log.parent.mkdir(parents=True, exist_ok=True)
        a.log.write_text(json.dumps({"non_equivalent": n - eq, "equivalent": eq,
                                     "defects": total_surv, "ok": total_surv == 0, "rows": rows},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if total_surv else 0


if __name__ == "__main__":
    sys.exit(main())
