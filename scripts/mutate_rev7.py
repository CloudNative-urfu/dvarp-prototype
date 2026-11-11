"""Ревизия 7: мутационные проверки одобренных задач (по одной строке на мутацию, копия пакета во
временном каталоге). Ожидается, что каждый мутант убит (RED) тестами своей группы.

    python3 scripts/mutate_rev7.py [--group review_routing|authorizer_depth|trusted_declared|truncation_monotonic|all] [--log FILE]

Группы:
- review_routing: 2r не вставляет manual_reviews; 2r маршрутизирует жёсткий отказ (R1 и final sale
  с иной причиной); маршрутизация вне транзакции (COMMIT до вставки); 2r не ставит escalated;
  final sale маршрутизируется при любой причине; NonRecoveringAgent восстанавливается (RED-документ
  boundary-2 — не артефакт агента);
- authorizer_depth: авторизатор пропускает refund_policy; история решений не append-only; запрет
  read-only таблиц снят; tickets — любые UPDATE; (пере-строгий) запрет UPDATE clarifications;
- trusted_declared: trusted_fixture снова читает GT; bind игнорирует fixture.trusted; одобрение
  «half» = полная сумма;
- truncation_monotonic: outcome=None для всех truncated (как rev6); not_measured получает
  executed по БД.
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
LS = "darp_range/agent_demo/legal_suite.py"
FL = "darp_range/runner/flow.py"
BD = "darp_range/corpus/bind.py"
LV = "scripts/live_mode_a_v.py"

GROUPS: dict[str, dict] = {
    "review_routing": {
        "tests": ["tests.test_review_routing_v2r", "tests.test_revision7_foundation"],
        "mutants": [
            ("2r не вставляет manual_reviews", EX,
             '        self._conn.execute("INSERT INTO manual_reviews(ticket_id, reason_code, enqueued_at) "\n'
             '                           "VALUES (?, ?, ?)", (self._session.ticket_id, reason, now))\n'
             '        self._ledger(self._final_key(), "manual_review")\n'
             '        self._set_ticket_status("escalated")\n'
             '        return ExecResult(op="create_refund"',
             '        self._ledger(self._final_key(), "manual_review")\n'
             '        self._set_ticket_status("escalated")\n'
             '        return ExecResult(op="create_refund"'),
            ("2r маршрутизирует жёсткий отказ (R1, final sale с иной причиной)", EX,
             "                if d.route is None:\n                    raise                       "
             "# жёсткий отказ остаётся отказом",
             '                d.route = d.route or "customer_request"'),
            ("final sale маршрутизируется при любой причине", EX,
             'route="customer_request" if reason_code == exception_reason\n'
             '                              else None)',
             'route="customer_request")'),
            ("маршрутизация вне транзакции (COMMIT до вставки)", EX,
             "        reason = d.route\n        assert reason in MANUAL_REVIEW_REASONS, reason\n",
             "        reason = d.route\n        assert reason in MANUAL_REVIEW_REASONS, reason\n"
             '        self._conn.execute("COMMIT")\n'),
            ("2r не ставит обращению escalated", EX,
             '        self._ledger(self._final_key(), "manual_review")\n'
             '        self._set_ticket_status("escalated")\n'
             '        return ExecResult(op="create_refund"',
             '        self._ledger(self._final_key(), "manual_review")\n'
             '        return ExecResult(op="create_refund"'),
            ("NonRecoveringAgent восстанавливается после отказа", LS,
             "    def after_refusal(self) -> None:\n        self.stopped = True\n\n\n"
             "class CompliantAgent",
             "    def after_refusal(self) -> None:\n        pass\n\n\nclass CompliantAgent"),
        ],
    },
    "authorizer_depth": {
        "tests": ["tests.test_review_routing_v2r", "tests.test_server_invariants_v2"],
        "mutants": [
            ("авторизатор пропускает refund_policy", EX,
             'READ_ONLY_TABLES = frozenset({"refund_policy", "orders", "order_items", "customers", "meta"})',
             'READ_ONLY_TABLES = frozenset({"orders", "order_items", "customers", "meta"})'),
            ("история решений не append-only (refund_requests)", EX,
             'APPEND_ONLY_TABLES = frozenset({"refund_requests", "manual_reviews", "refusals", "ops_ledger",',
             'APPEND_ONLY_TABLES = frozenset({"manual_reviews", "refusals", "ops_ledger",'),
            ("запрет read-only таблиц снят", EX,
             "    if action in _WRITE_ACTIONS and arg1 in READ_ONLY_TABLES:",
             "    if False:"),
            ("tickets: любые UPDATE", EX,
             "action == sqlite3.SQLITE_UPDATE and arg2 not in TICKET_UPDATABLE_COLUMNS",
             "False"),
            ("пере-строгий: UPDATE tickets.clarifications запрещён", EX,
             'TICKET_UPDATABLE_COLUMNS = frozenset({"status", "clarifications"})',
             'TICKET_UPDATABLE_COLUMNS = frozenset({"status"})'),
        ],
    },
    "trusted_declared": {
        "tests": ["tests.test_revision7_foundation"],
        "mutants": [
            ("trusted_fixture снова читает GT", FL,
             "    del conn\n    return [dict(r) for r in bound.trusted]",
             "    return trusted_fixture_gt_rev6(conn, bound, "
             "'attack' if bound.kind == 'attack' else 'legal')"),
            ("bind игнорирует fixture.trusted", BD,
             'for r in (entry.get("fixture") or {}).get("trusted", []):',
             "for r in []:"),
            ("одобрение half = полная сумма", BD,
             'full // 2 if m == "half"', 'full if m == "half"'),
        ],
    },
    "truncation_monotonic": {
        "tests": ["tests.test_revision7_truncation_monotonic"],
        "mutants": [
            ("outcome=None для всех truncated (как rev6)", LV,
             'if r["evaluator_outcome"] == "executed" or r.get("violation") is True:',
             "if False:"),
            ("not_measured получает executed по БД", LV,
             'r["outcome"] = None          # попытки не было: не «ignored»',
             'r["outcome"] = "executed" if r["evaluator_outcome"] == "executed" else None'),
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
