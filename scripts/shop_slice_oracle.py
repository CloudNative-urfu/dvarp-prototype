"""Независимый oracle контура возвратов (ревизия 11, `returns-slice-e2e`).

    python3 scripts/shop_slice_oracle.py --db SLICE.sqlite --ledger LEDGER.sqlite \
        [--observations OBS.jsonl] [--out REPORT.json]

Читает БД контура и книгу эмулятора напрямую в режиме `mode=ro` (только stdlib, не
импортирует `darp_range`, в том числе `shop_svc` и `pay_emu`: решение принимает не тот код,
который его проверяет). Инварианты:

- I1 по каждой заявке (`refund_requests`, metadata.refund_request_id в книге) — не больше одного
  succeeded-возврата в книге и не больше одного не-failed (ещё pending второй выплаты — тоже
  двойная выплата, только незавершённая);
- I2 сумма каждого не-failed возврата книги = `refund_requests.amount_cents` его заявки;
  возврат книги без заявки контура — нарушение;
- I3 клиент не видел `refunded` без succeeded: для каждого наблюдения статуса (`--observations`:
  строки `{ticket_id, status, ledger_snapshot}`; снимок книги сделан сразу после наблюдения,
  succeeded — конечное состояние) по заявкам обращения в снимке есть succeeded-возврат;
- I4 каждое `resolved` обращение с `manual_reviews` имеет решение оператора approve
  (`operator_decisions`), каждое `refused` с `manual_reviews` — reject;
- I5 получатель каждого возврата книги = `payee_ref` платежа заказа позиции заявки
  (`charges.order_id` = заказ позиции; ревью rev11 R3);
- I6 сумма заявки оператора ≤ суммы его решения, и ровно одна заявка на решение approve;
- I7 журнал книги монотонен: по каждому возврату первое событие `→ pending`, затем не больше
  одного перехода в конечное состояние, итог совпадает с `refunds.state`;
- I8 `payouts.state = succeeded` только если в книге есть succeeded-возврат этой заявки;
  `payouts.state = failed` — только если нет succeeded (выплата не потеряна молча).

Код возврата 0 — PASS, 1 — FAIL (нарушения перечислены), 2 — ошибка входа.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def ro(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    c = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=10.0)
    c.execute("PRAGMA query_only = ON")
    return c


def _ledger_refunds(led: sqlite3.Connection) -> list[dict]:
    rows = led.execute("SELECT id, charge_id, amount_cents, payee_ref, idempotency_key, "
                       "metadata_json, state FROM refunds ORDER BY seq").fetchall()
    out = []
    for rid, ch, amt, payee, key, meta, state in rows:
        rr = json.loads(meta).get("refund_request_id")
        out.append({"id": rid, "charge_id": ch, "amount_cents": amt, "payee_ref": payee,
                    "key": key, "refund_request_id": rr, "state": state})
    return out


def check(db: str | Path, ledger: str | Path, observations: str | Path | None = None) -> dict:
    shop, led = ro(db), ro(ledger)
    v: list[dict] = []

    def bad(inv: str, **detail) -> None:
        v.append({"invariant": inv, **detail})

    try:
        rrs = {r[0]: {"ticket_id": r[1], "order_item_id": r[2], "amount_cents": r[3],
                      "created_by": r[4], "order_id": r[5]}
               for r in shop.execute(
                   "SELECT r.id, r.ticket_id, r.order_item_id, r.amount_cents, r.created_by, "
                   "i.order_id FROM refund_requests r JOIN order_items i ON i.id = "
                   "r.order_item_id")}
        refunds = _ledger_refunds(led)
        charges = {r[0]: {"order_id": r[1], "payee_ref": r[2]} for r in led.execute(
            "SELECT charge_id, order_id, payee_ref FROM charges")}
        by_rr: dict = defaultdict(list)
        for f in refunds:
            by_rr[f["refund_request_id"]].append(f)
        # I1
        for rr, fs in by_rr.items():
            n = sum(1 for f in fs if f["state"] == "succeeded")
            live = sum(1 for f in fs if f["state"] != "failed")
            if n > 1 or live > 1:
                bad("I1_single_success", refund_request_id=rr, succeeded=n, not_failed=live,
                    refunds=[f["id"] for f in fs])
        # I2, I5
        for f in refunds:
            rr = rrs.get(f["refund_request_id"])
            if rr is None:
                bad("I2_amount_matches_request", refund=f["id"],
                    detail="ledger refund without slice refund request")
                continue
            if f["state"] != "failed" and f["amount_cents"] != rr["amount_cents"]:
                bad("I2_amount_matches_request", refund=f["id"], ledger=f["amount_cents"],
                    request=rr["amount_cents"], refund_request_id=f["refund_request_id"])
            ch = charges.get(f["charge_id"])
            if ch is None or ch["order_id"] != rr["order_id"] or \
                    f["payee_ref"] != ch["payee_ref"]:
                bad("I5_payee_of_charge", refund=f["id"], payee=f["payee_ref"],
                    charge=f["charge_id"], expected_order=rr["order_id"],
                    expected_payee=(ch or {}).get("payee_ref"))
        # I4
        for tid, status in shop.execute(
                "SELECT t.id, t.status FROM tickets t JOIN manual_reviews m ON m.ticket_id = t.id "
                "WHERE t.status IN ('resolved', 'refused')"):
            want = "approve" if status == "resolved" else "reject"
            if not shop.execute("SELECT 1 FROM operator_decisions WHERE ticket_id = ? AND "
                                "decision = ?", (tid, want)).fetchone():
                bad("I4_escalation_has_operator_decision", ticket_id=tid, status=status)
        # I6
        for tid, did, amt, rr in shop.execute(
                "SELECT ticket_id, id, amount_cents, refund_request_id FROM operator_decisions "
                "WHERE decision = 'approve'"):
            reqs = [k for k, r in rrs.items() if r["ticket_id"] == tid and
                    r["created_by"] == "operator"]
            if reqs != [rr] or rrs[rr]["amount_cents"] > amt:
                bad("I6_request_within_decision", ticket_id=tid, decision=did,
                    decision_amount=amt, requests=reqs)
        # I7
        state_of = {f["id"]: f["state"] for f in refunds}
        events: dict = defaultdict(list)
        for rid, fr, to in led.execute("SELECT refund_id, from_state, to_state FROM "
                                       "ledger_events ORDER BY id"):
            events[rid].append((fr, to))
        for rid, st in state_of.items():
            ev = events.get(rid, [])
            terminal = [e for e in ev if e[1] in ("succeeded", "failed")]
            ok = (bool(ev) and ev[0] == (None, "pending") and len(terminal) <= 1
                  and all(e[0] == "pending" for e in terminal)
                  and (terminal[0][1] if terminal else "pending") == st
                  and len(ev) == 1 + len(terminal))
            if not ok:
                bad("I7_ledger_monotonic", refund=rid, state=st, events=ev)
        # I8
        for pid, rr, st in shop.execute("SELECT id, refund_request_id, state FROM payouts"):
            succ = any(f["state"] == "succeeded" for f in by_rr.get(rr, []))
            if st == "succeeded" and not succ:
                bad("I8_payout_state_backed_by_ledger", payout_id=pid, refund_request_id=rr,
                    state=st)
            if st == "failed" and succ:
                bad("I8_payout_state_backed_by_ledger", payout_id=pid, refund_request_id=rr,
                    state=st, detail="ledger has succeeded refund")
        # I3
        n_obs = 0
        if observations:
            for line in Path(observations).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                o = json.loads(line)
                n_obs += 1
                if o["status"] != "refunded":
                    continue
                snap = ro(o["ledger_snapshot"])
                try:
                    snap_refunds = _ledger_refunds(snap)
                finally:
                    snap.close()
                ticket_rrs = {k for k, r in rrs.items() if r["ticket_id"] == o["ticket_id"]}
                if not ticket_rrs or not all(
                        any(f["refund_request_id"] == k and f["state"] == "succeeded"
                            for f in snap_refunds) for k in ticket_rrs):
                    bad("I3_refunded_only_after_success", ticket_id=o["ticket_id"],
                        snapshot=str(o["ledger_snapshot"]))
        summary = {"refund_requests": len(rrs), "ledger_refunds": len(refunds),
                   "ledger_succeeded": sum(1 for f in refunds if f["state"] == "succeeded"),
                   "observations": n_obs}
    finally:
        shop.close()
        led.close()
    return {"ok": not v, "verdict": "PASS" if not v else "FAIL", "violations": v,
            "summary": summary, "oracle": "scripts/shop_slice_oracle.py",
            "invariants": ["I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--observations", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    try:
        res = check(a.db, a.ledger, a.observations)
    except (FileNotFoundError, sqlite3.DatabaseError, KeyError, ValueError) as exc:
        print(json.dumps({"ok": False, "verdict": "ERROR", "error": f"{type(exc).__name__}: "
                                                                    f"{exc}"}))
        return 2
    text = json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
