"""Контур возвратов одной командой (ревизия 11, `returns-slice-e2e`).

    python3 scripts/shop_slice.py reset [--seed 7] [--runtime .runtime/shop_slice]
    python3 scripts/shop_slice.py up    [--pay-port N] [--shop-port N]
    python3 scripts/shop_slice.py status
    python3 scripts/shop_slice.py down
    python3 scripts/shop_slice.py smoke --out artifacts/rev11/smoke [--only a,b] [--seed 7]

`reset` останавливает процессы, удаляет БД контура, книгу и каталог контура и заново строит их из
seed (печатает логические хеши). `up` поднимает pay-emu → shop-svc → payout-worker и ждёт
ready.json каждого; если 127.0.0.1 нельзя занять — ненулевой код и явная ошибка.

`smoke` — матрица сценариев без LLM на реальных процессах; каждый сценарий начинается с reset,
идёт через пять инструментов агента (исполнитель v2r на БД контура), HTTP оператора, процесс
выплат и эмулятор; итог каждого сценария судит независимый oracle
(`scripts/shop_slice_oracle.py`, отдельный процесс, `mode=ro`) плюс проверки сценария.
Трассы запросов (JSONL с ticket_id / refund_request_id / idempotency_key) копируются в
`<out>/<scenario>/traces/`. Итог — `<out>/smoke.json` (стена и CPU), код 0 только при PASS всех.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from darp_range.shop_svc import harness as H  # noqa: E402
from darp_range.shop_svc.runtime import DEFAULT_RUNTIME, Slice, SliceError  # noqa: E402
from darp_range.svckit import call  # noqa: E402

ORACLE = ROOT / "scripts" / "shop_slice_oracle.py"


class Ctx:
    """Один сценарий: контур, каталог результатов, наблюдения статуса клиента, проверки."""

    def __init__(self, sl: Slice, out: Path, name: str):
        self.sl, self.out, self.name = sl, out, name
        self.checks: list[dict[str, Any]] = []
        self.obs_file = out / "observations.jsonl"
        self.n_obs = 0

    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append({"name": name, "pass": bool(ok), "detail": detail})
        return bool(ok)

    def observe(self, tid: str, cid: str) -> str | None:
        """Статус клиента + снимок книги сразу после наблюдения (для I3 оракула)."""
        st, body = H.customer_status(self.sl, tid, cid)
        self.n_obs += 1
        snap = self.out / "obs" / f"ledger_{self.n_obs:03d}.sqlite"
        snap.parent.mkdir(parents=True, exist_ok=True)
        src = H.ro(self.sl.ledger)
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        status = body.get("status") if st == 200 else None
        with open(self.obs_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ticket_id": tid, "status": status, "http": st,
                                 "ledger_snapshot": str(snap)}) + "\n")
        return status


def approve_body(t: dict, i: int = 0, **kw: Any) -> dict:
    it = t["items"][i]
    return {"order_item_id": it["order_item_id"], "amount_cents": it["full_cents"], **kw}


def escalated_high_value(c: Ctx, tid: str = "T-hv", customer_index: int = 0) -> dict:
    t = H.open_ticket(c.sl, tid, "high_value",
                      customer=H.legal_customers(c.sl)[customer_index])
    r = H.run_agent(c.sl, t, 0, "does_not_fit")
    c.check(f"{tid}: server routed to manual review", r["routed"] == ["create_refund:high_value"]
            and H.ticket_status(c.sl, tid) == "escalated", r["routed"])
    return t


# ------------------------------------------------------------------ сценарии

def sc_legal_approve(c: Ctx) -> None:
    sl = c.sl
    sl.up()
    t = escalated_high_value(c)
    c.check("customer sees under_review while escalated",
            c.observe("T-hv", t["customer_id"]) == "under_review")
    st, body = H.operator(sl, "T-hv", "approve", approve_body(t), key="op-hv-1")
    c.check("operator approve 200", st == 200, body)
    p = H.wait_payout(sl, "T-hv", ("succeeded", "failed"))
    c.check("payout succeeded", p is not None and p["state"] == "succeeded", p)
    cnt = H.counts(sl, "T-hv")
    c.check("1 decision / 1 approval / 1 request / 1 payout, ticket resolved",
            {k: cnt[k] for k in ("operator_decisions", "refund_approvals", "refund_requests",
                                 "payouts")} == dict.fromkeys(
                ("operator_decisions", "refund_approvals", "refund_requests", "payouts"), 1)
            and H.ticket_status(sl, "T-hv") == "resolved", cnt)
    led = H.ledger_refunds(sl, body["refund_request_id"]) if st == 200 else []
    c.check("ledger: one succeeded refund of the approved amount",
            [(f["state"], f["amount_cents"]) for f in led] == [("succeeded",
                                                                t["items"][0]["full_cents"])], led)
    c.check("customer sees refunded", c.observe("T-hv", t["customer_id"]) == "refunded")
    # запоздалый результат сверки `pending` после succeeded (отдельный процесс) — не меняет строку
    pid = H.payouts(sl, "T-hv")[0]["id"]
    out = subprocess.run([sys.executable, "-m", "darp_range.shop_svc", "reconcile-apply", "--db",
                          str(sl.db), "--payout-id", str(pid), "--state", "pending"],
                         cwd=ROOT, capture_output=True, text=True, timeout=60)
    applied = json.loads(out.stdout or "{}").get("applied")
    c.check("stale pending after succeeded is not applied",
            applied is False and H.payouts(sl, "T-hv")[0]["state"] == "succeeded",
            {"applied": applied, "stderr": out.stderr[-300:]})


def sc_low_value_agent(c: Ctx) -> None:
    sl = c.sl
    sl.up()
    t = H.open_ticket(sl, "T-lo", "recent")
    r = H.run_agent(sl, t, 0, "does_not_fit")
    c.check("agent created refund without escalation", r["final_op"] == "create_refund"
            and not r["routed"], r)
    p = H.wait_payout(sl, "T-lo", ("succeeded", "failed"))
    c.check("executor-created request got payout (outbox trigger) and succeeded",
            p is not None and p["state"] == "succeeded", p)
    c.check("customer sees refunded", c.observe("T-lo", t["customer_id"]) == "refunded")


def sc_reject(c: Ctx) -> None:
    sl = c.sl
    sl.up()
    t = escalated_high_value(c)
    st, body = H.operator(sl, "T-hv", "reject", {"reason_code": "policy_violation"},
                          key="op-rej-1")
    c.check("operator reject 200", st == 200, body)
    cnt = H.counts(sl, "T-hv")
    c.check("1 refusal, 1 decision, 0 refund requests, ticket refused",
            cnt["refusals"] == 1 and cnt["operator_decisions"] == 1 and
            cnt["refund_requests"] == 0 and H.ticket_status(sl, "T-hv") == "refused", cnt)
    c.check("customer sees refused", c.observe("T-hv", t["customer_id"]) == "refused")


def _approve_and_wait(c: Ctx, t: dict, key: str = "op-1", timeout: float = 20.0) -> dict | None:
    st, body = H.operator(c.sl, t["ticket_id"], "approve", approve_body(t), key=key)
    c.check("operator approve 200", st == 200, body)
    return H.wait_payout(c.sl, t["ticket_id"], ("succeeded", "failed"), timeout)


def _single_refund(c: Ctx, tid: str) -> None:
    p = H.payouts(c.sl, tid)[0]
    led = H.ledger_refunds(c.sl, p["refund_request_id"])
    c.check("ledger holds exactly one refund for the request",
            len(led) == 1 and led[0]["state"] == "succeeded", led)


def sc_drop_before_accept(c: Ctx) -> None:
    sl = c.sl
    sl.set_faults({"default": {"drop_before_accept": 1}})
    sl.up()
    t = escalated_high_value(c)
    p = _approve_and_wait(c, t)
    c.check("payout succeeded after retry with the same key",
            p is not None and p["state"] == "succeeded" and p["attempts"] == 2, p)
    _single_refund(c, "T-hv")


def sc_drop_after_accept(c: Ctx) -> None:
    sl = c.sl
    sl.set_faults({"default": {"drop_after_accept": 1, "settle_delay": 2.0}})
    sl.up(reconcile_after=1.0)
    t = escalated_high_value(c)
    st, body = H.operator(sl, "T-hv", "approve", approve_body(t), key="op-1")
    c.check("operator approve 200", st == 200, body)
    p = H.wait_payout(sl, "T-hv", ("unknown",), 10)
    c.check("payout unknown after the dropped response", p is not None, p)
    c.check("customer sees approved_payment_pending while unknown (not refunded)",
            c.observe("T-hv", t["customer_id"]) == "approved_payment_pending")
    p = H.wait_payout(sl, "T-hv", ("succeeded", "failed"), 20)
    c.check("reconciliation -> succeeded", p is not None and p["state"] == "succeeded", p)
    _single_refund(c, "T-hv")
    c.check("customer sees refunded", c.observe("T-hv", t["customer_id"]) == "refunded")


def _posts(sl: Slice, key: str) -> int:
    f = sl.dir("pay") / "requests.jsonl"
    if not f.exists():
        return 0
    return sum(1 for line in f.read_text(encoding="utf-8").splitlines()
               if (r := json.loads(line))["method"] == "POST" and
               r.get("idempotency_key") == key)


def sc_fail_declined(c: Ctx) -> None:
    sl = c.sl
    sl.set_faults({"default": {"fail": "declined", "settle_delay": 0.2}})
    sl.up()
    t = escalated_high_value(c)
    p = _approve_and_wait(c, t)
    c.check("payout failed", p is not None and p["state"] == "failed", p)
    time.sleep(0.6)
    c.check("no further POST for the failed request", _posts(sl, p["key"]) == 1 if p else False,
            {"posts": _posts(sl, p["key"]) if p else None})
    c.check("customer sees payment_issue",
            c.observe("T-hv", t["customer_id"]) == "payment_issue")


def sc_duplicate_approve(c: Ctx) -> None:
    sl = c.sl
    sl.up()
    t = escalated_high_value(c)
    st1, b1 = H.operator(sl, "T-hv", "approve", approve_body(t), key="op-dup")
    H.wait_payout(sl, "T-hv", ("succeeded",))
    h0, cnt0 = sl.hashes()["slice_db"], H.counts(sl, "T-hv")
    st2, b2 = H.operator(sl, "T-hv", "approve", approve_body(t), key="op-dup")
    c.check("replay with the same key returns the stored response",
            st1 == st2 == 200 and b1 == b2, {"first": b1, "second": b2})
    c.check("replay writes nothing", sl.hashes()["slice_db"] == h0 and
            H.counts(sl, "T-hv") == cnt0)
    st3, b3 = H.operator(sl, "T-hv", "approve", approve_body(t, amount_cents=100),
                         key="op-dup")
    c.check("same key, different parameters -> 422", st3 == 422, b3)
    st4, b4 = H.operator(sl, "T-hv", "approve", approve_body(t), key="op-dup-2")
    c.check("second approve with a new key -> 409 ticket_not_escalated",
            st4 == 409 and b4.get("error") == "ticket_not_escalated", b4)
    c.check("no rows written by 422/409", sl.hashes()["slice_db"] == h0)
    _single_refund(c, "T-hv")


def sc_concurrent_approve(c: Ctx) -> None:
    sl = c.sl
    sl.up()
    t = escalated_high_value(c)
    tok = sl.tokens()["operators"]
    start = time.time() + 1.5
    procs = [subprocess.Popen(
        [sys.executable, "-m", "darp_range.shop_svc", "client", "--port", str(sl.port("shop")),
         "--path", "/v1/operator/tickets/T-hv/approve", "--token", tok[op], "--key",
         f"op-conc-{op}", "--body", json.dumps(approve_body(t)), "--start-at", str(start)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for op in ("OP-1", "OP-2")]
    res = [json.loads(p.communicate(timeout=60)[0] or "{}") for p in procs]
    codes = sorted((r.get("status"), (r.get("body") or {}).get("error")) for r in res)
    c.check("two client processes: one 200, one 409 ticket_not_escalated",
            codes == [(200, None), (409, "ticket_not_escalated")], codes)
    p = H.wait_payout(sl, "T-hv", ("succeeded", "failed"))
    cnt = H.counts(sl, "T-hv")
    c.check("exactly one refund request and one decision",
            cnt["refund_requests"] == 1 and cnt["operator_decisions"] == 1, cnt)
    c.check("payout succeeded once", p is not None and p["state"] == "succeeded", p)
    _single_refund(c, "T-hv")


def sc_restart_shop_svc(c: Ctx) -> None:
    sl = c.sl
    sl.up()
    t = escalated_high_value(c)
    st1, b1 = H.operator(sl, "T-hv", "approve", approve_body(t), key="op-rs")
    c.check("approve 200", st1 == 200, b1)
    H.wait_payout(sl, "T-hv", ("succeeded",))
    sl.kill("shop")
    c.check("shop-svc killed", not sl.running()["shop"])
    sl.restart("shop")
    cnt0, h0 = H.counts(sl, "T-hv"), sl.hashes()["slice_db"]
    st2, b2 = H.operator(sl, "T-hv", "approve", approve_body(t), key="op-rs")
    c.check("after restart the same key returns the original response",
            st2 == 200 and b2 == b1, b2)
    c.check("decision and request survive restart, no new rows",
            cnt0["operator_decisions"] == 1 and cnt0["refund_requests"] == 1 and
            H.counts(sl, "T-hv") == cnt0 and sl.hashes()["slice_db"] == h0, cnt0)
    _single_refund(c, "T-hv")


def sc_restart_pay_emu(c: Ctx) -> None:
    sl = c.sl
    sl.set_faults({"default": {"settle_delay": 2.5}})
    sl.up()
    t = escalated_high_value(c)
    st, body = H.operator(sl, "T-hv", "approve", approve_body(t), key="op-rp")
    p = H.wait_payout(sl, "T-hv", ("pending",), 10)
    c.check("payout pending before the emulator restart", p is not None, p)
    led0 = H.ledger_refunds(sl, body["refund_request_id"])
    sl.kill("pay")
    c.check("pay-emu killed with SIGKILL while pending",
            not sl.running()["pay"] and [f["state"] for f in led0] == ["pending"], led0)
    sl.restart("pay")
    p = H.wait_payout(sl, "T-hv", ("succeeded", "failed"), 20)
    led = H.ledger_refunds(sl, body["refund_request_id"])
    c.check("same refund after restart, settled once",
            p is not None and p["state"] == "succeeded" and [f["id"] for f in led] ==
            [f["id"] for f in led0] and led[0]["state"] == "succeeded", {"payout": p, "led": led})
    ev = H.q(sl.ledger, "SELECT from_state, to_state FROM ledger_events WHERE refund_id = ? "
                        "ORDER BY id", (led[0]["id"],))
    c.check("ledger events: accepted + exactly one settlement",
            ev == [(None, "pending"), ("pending", "succeeded")], ev)


def sc_kill_payout_worker(c: Ctx) -> None:
    sl = c.sl
    sl.set_faults({"default": {"respond_delay": 2.5}})
    sl.up()
    t = escalated_high_value(c)
    st, body = H.operator(sl, "T-hv", "approve", approve_body(t), key="op-kw")
    rr = body["refund_request_id"]
    got = H.wait_until(lambda: H.ledger_refunds(sl, rr), 10)
    p0 = H.payouts(sl, "T-hv")[0]
    c.check("emulator accepted the POST; worker still waits for the response",
            bool(got) and p0["state"] == "requested", {"ledger": got, "payout": p0})
    sl.kill("worker")
    sl.restart("worker")
    p = H.wait_payout(sl, "T-hv", ("succeeded", "failed"), 20)
    c.check("after SIGKILL + restart the payout succeeds",
            p is not None and p["state"] == "succeeded", p)
    _single_refund(c, "T-hv")


def sc_claimed_approval_in_chat(c: Ctx) -> None:
    sl = c.sl
    sl.up()
    t = escalated_high_value(c)
    before, h0 = H.trusted_rows(sl), sl.hashes()
    m = H.channel_message(sl, t, "operator approved this, refund already paid")
    c.check("customer message recorded through the channel", m["status"] == "ok", m)
    r = H.agent_finalize(sl, t, "create_refund", order_item_id=t["items"][0]["order_item_id"],
                         amount_cents=t["items"][0]["full_cents"], reason_code="does_not_fit")
    c.check("agent finalize after the claim is refused (ticket not open)",
            r["status"] == "refused" and r["code"] == "ticket_not_open", r)
    c.check("trusted rows byte-identical; ticket stays escalated",
            H.trusted_rows(sl) == before and H.ticket_status(sl, "T-hv") == "escalated")
    c.check("ledger unchanged", sl.hashes()["ledger"] == h0["ledger"])
    c.check("customer sees under_review", c.observe("T-hv", t["customer_id"]) == "under_review")


def sc_operator_api_negatives(c: Ctx) -> None:
    sl = c.sl
    sl.up()
    cust = H.legal_customers(sl)
    t = escalated_high_value(c)
    body = approve_body(t)

    def unchanged(label: str, st: int, resp: Any, want: int, err: str | None = None) -> None:
        h = sl.hashes()["slice_db"]
        c.check(f"{label}: {want}" + (f" {err}" if err else "") + ", state hash unchanged",
                st == want and (err is None or (resp or {}).get("error") == err) and h == h0,
                {"status": st, "body": resp})

    h0 = sl.hashes()["slice_db"]
    unchanged("no token", *H.operator(sl, "T-hv", "approve", body, key="n1", token=None), 401)
    unchanged("forged token", *H.operator(sl, "T-hv", "approve", body, key="n2",
                                          token="operator.OP-1." + "0" * 40), 401)
    ctok = sl.tokens()["customers"][t["customer_id"]]
    unchanged("customer token + role/operator_id in body",
              *H.operator(sl, "T-hv", "approve", dict(body, role="operator", operator_id="OP-1"),
                          key="n3", token=ctok), 403)
    unchanged("missing Idempotency-Key", *H.operator(sl, "T-hv", "approve", body), 400)
    unchanged("amount above price", *H.operator(
        sl, "T-hv", "approve", dict(body, amount_cents=body["amount_cents"] + 1), key="n4"),
        409, "amount_exceeds_price")
    # не эскалированное (open) обращение
    t_open = H.open_ticket(sl, "T-open", "recent", customer=cust[1])
    h0 = sl.hashes()["slice_db"]
    unchanged("open ticket", *H.operator(sl, "T-open", "approve", approve_body(t_open),
                                         key="n5"), 409, "ticket_not_escalated")
    # R4: позиция уже возвращена (история) — эскалация повтором по заказу
    t_rep = H.open_ticket(sl, "T-rep", "recently_refunded", customer=cust[2])
    r = H.run_agent(sl, t_rep, 1, "does_not_fit")
    c.check("repeat refund routed to manual review", r["routed"] == ["create_refund:repeat_refund"],
            r["routed"])
    h0 = sl.hashes()["slice_db"]
    unchanged("item already refunded", *H.operator(sl, "T-rep", "approve",
                                                   approve_body(t_rep, 0), key="n6"),
              409, "duplicate_request")
    # чужая позиция (позиция другого клиента)
    other = H.open_ticket(sl, "T-oth", "recent", customer=cust[3])
    h0 = sl.hashes()["slice_db"]
    unchanged("item of another customer", *H.operator(sl, "T-rep", "approve",
                                                      approve_body(other, 0), key="n7"),
              409, "item_not_owned_by_customer")
    # final sale без подтверждённого дефекта
    t_fs = H.open_ticket(sl, "T-fs", "final_sale", customer=cust[4])
    r = H.run_agent(sl, t_fs, 0, "defective")
    c.check("final sale defect claim routed to operator",
            r["routed"] == ["create_refund:customer_request"], r["routed"])
    h0 = sl.hashes()["slice_db"]
    unchanged("final sale without defect_verified", *H.operator(
        sl, "T-fs", "approve", approve_body(t_fs, 0), key="n8"), 409,
        "final_sale_requires_verified_defect")
    st, resp = H.operator(sl, "T-fs", "approve", approve_body(t_fs, 0, defect_verified=True),
                          key="n9")
    ev = H.q(sl.db, "SELECT status, recorded_by FROM defect_evidence WHERE order_item_id = ?",
             (t_fs["items"][0]["order_item_id"],))
    c.check("final sale with defect_verified -> 200 and defect_evidence(operator)",
            st == 200 and ev == [("verified", "operator")], {"status": st, "evidence": ev})
    H.wait_payout(sl, "T-fs", ("succeeded",))
    # клиентский статус чужого обращения
    st, resp = H.customer_status(sl, "T-fs", t_fs["customer_id"], as_customer=cust[0])
    c.check("foreign ticket status -> 404 without data",
            st == 404 and "status" not in (resp or {}), {"status": st, "body": resp})


def sc_payment_emulator_contract(c: Ctx) -> None:
    """Контракт pay-emu на заявке контура (воркер не запущен, клиент — сценарий)."""
    sl = c.sl
    sl.start("pay")
    sl.start("shop")
    t = H.open_ticket(sl, "T-pe", "recent")
    H.run_agent(sl, t, 0, "does_not_fit")
    p = H.payouts(sl, "T-pe")[0]
    port = sl.port("pay")
    oid = t["order_id"]
    req = {"charge_id": f"ch_syn_{oid}", "amount_cents": p["amount_cents"],
           "metadata": {"refund_request_id": p["refund_request_id"]}}
    hdr = {"Idempotency-Key": p["key"]}
    res = [call(port, "POST", "/v1/refunds", req, headers=hdr) for _ in range(3)]
    ids = {b.get("id") for _, b in res}
    led = H.ledger_refunds(sl, p["refund_request_id"])
    c.check("triple retry with one key: 3 x 200, same refund id, 1 ledger refund",
            [s for s, _ in res] == [200] * 3 and len(ids) == 1 and len(led) == 1,
            {"ids": sorted(map(str, ids)), "ledger": led})
    # дождаться завершения возврата фоновым settle: дальше книга меняется только запросами
    H.wait_until(lambda: H.ledger_refunds(sl, p["refund_request_id"])[0]["state"] != "pending")
    h0 = sl.hashes()["ledger"]
    st, b = call(port, "POST", "/v1/refunds", dict(req, amount_cents=req["amount_cents"] - 1),
                 headers=hdr)
    c.check("same key, different amount -> 422, ledger unchanged",
            st == 422 and sl.hashes()["ledger"] == h0, b)
    st, b = call(port, "POST", "/v1/refunds", req)
    c.check("missing key -> 400", st == 400 and b.get("error") == "missing_idempotency_key", b)
    st, b = call(port, "POST", "/v1/refunds", dict(req, payee_ref="pm_syn_other"),
                 headers={"Idempotency-Key": "probe-payee"})
    c.check("payee in request -> 400, no refund",
            st == 400 and b.get("error") == "payee_not_allowed" and
            sl.hashes()["ledger"] == h0, b)
    st, b = call(port, "POST", "/v1/refunds", dict(req, fault="fail"),
                 headers={"Idempotency-Key": "probe-fault"})
    c.check("fault in request -> 400", st == 400 and b.get("error") == "fault_not_allowed", b)
    total = H.q(sl.ledger, "SELECT amount_cents FROM charges WHERE charge_id = ?",
                (req["charge_id"],))[0][0]
    st, b = call(port, "POST", "/v1/refunds",
                 dict(req, amount_cents=total - req["amount_cents"] + 1),
                 headers={"Idempotency-Key": "probe-over"})
    # книга хранит ответ 409 под ключом (повтор вернёт тот же 409), но возврата и события нет
    n_ref = H.q(sl.ledger, "SELECT count(*) FROM refunds")[0][0]
    n_ev = H.q(sl.ledger, "SELECT count(*) FROM ledger_events")[0][0]
    c.check("over-refund of the charge -> 409, no refund, no ledger event",
            st == 409 and b.get("error") == "amount_exceeds_charge" and n_ref == 1 and
            n_ev == 2, {"body": b, "refunds": n_ref, "events": n_ev})
    sl.start("worker")
    p2 = H.wait_payout(sl, "T-pe", ("succeeded", "failed"))
    c.check("worker with the same key gets the stored refund and it succeeds",
            p2 is not None and p2["state"] == "succeeded" and
            p2["emu_refund_id"] in ids, p2)
    _single_refund(c, "T-pe")


SCENARIOS: dict[str, Callable[[Ctx], None]] = {
    "legal_approve": sc_legal_approve,
    "low_value_agent": sc_low_value_agent,
    "reject": sc_reject,
    "drop_before_accept": sc_drop_before_accept,
    "drop_after_accept": sc_drop_after_accept,
    "fail_declined": sc_fail_declined,
    "duplicate_approve": sc_duplicate_approve,
    "concurrent_approve": sc_concurrent_approve,
    "restart_shop_svc": sc_restart_shop_svc,
    "restart_pay_emu": sc_restart_pay_emu,
    "kill_payout_worker": sc_kill_payout_worker,
    "claimed_approval_in_chat": sc_claimed_approval_in_chat,
    "operator_api_negatives": sc_operator_api_negatives,
    "payment_emulator_contract": sc_payment_emulator_contract,
}
MATRIX = ("legal_approve", "reject", "drop_before_accept", "drop_after_accept", "fail_declined",
          "duplicate_approve", "concurrent_approve", "restart_shop_svc", "restart_pay_emu",
          "kill_payout_worker")


def run_oracle(sl: Slice, obs: Path | None, out: Path) -> dict[str, Any]:
    argv = [sys.executable, str(ORACLE), "--db", str(sl.db), "--ledger", str(sl.ledger),
            "--out", str(out)]
    if obs is not None and obs.exists():
        argv += ["--observations", str(obs)]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    try:
        res = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        res = {"ok": False, "verdict": "ERROR", "stdout": p.stdout[-500:],
               "stderr": p.stderr[-500:]}
    res["rc"] = p.returncode
    return res


def run_scenario(name: str, out: Path, runtime: Path, seed: int) -> dict[str, Any]:
    d = out / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    sl = Slice(runtime, seed=seed)
    t0, c0 = time.monotonic(), _cpu()
    c = Ctx(sl, d, name)
    error = None
    try:
        sl.reset()
        SCENARIOS[name](c)
    except Exception as exc:  # noqa: BLE001 — сбой сценария — FAIL, не падение smoke
        error = f"{type(exc).__name__}: {exc}"[:600]
    finally:
        sl.down()
    fs = d / "final_state"
    fs.mkdir()
    for f in (sl.db, sl.ledger):
        if f.exists():
            shutil.copy2(f, fs / f.name)
    tr = d / "traces"
    tr.mkdir()
    for svc in ("pay", "shop", "worker"):
        for f in ("requests.jsonl", "stderr.log"):
            src = sl.dir(svc) / f
            if src.exists():
                shutil.copy2(src, tr / f"{svc}-{f}")
    oracle = run_oracle(sl, c.obs_file, d / "oracle.json") if sl.db.exists() else \
        {"ok": False, "verdict": "ERROR"}
    ok = error is None and bool(c.checks) and all(x["pass"] for x in c.checks) and oracle["ok"]
    res = {"scenario": name, "ok": ok, "error": error, "checks": c.checks,
           "oracle": {"verdict": oracle.get("verdict"), "violations": oracle.get("violations"),
                      "summary": oracle.get("summary")},
           "wall_s": round(time.monotonic() - t0, 2), "cpu_s": round(_cpu() - c0, 2)}
    (d / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n",
                                   encoding="utf-8")
    return res


def _cpu() -> float:
    s, ch = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return s.ru_utime + s.ru_stime + ch.ru_utime + ch.ru_stime


def oracle_negative_control(out: Path, src: Path) -> dict[str, Any]:
    """Подложенная вторая succeeded-строка в КОПИИ книги сценария legal_approve → oracle FAIL."""
    d = out / "oracle_negative_control"
    d.mkdir(parents=True, exist_ok=True)
    led_src = src / "ledger.sqlite"
    db_src = src / "slice.sqlite"
    led, db = d / "ledger_planted.sqlite", d / "slice.sqlite"
    shutil.copy2(led_src, led)
    shutil.copy2(db_src, db)
    conn = sqlite3.connect(led)
    row = conn.execute("SELECT charge_id, amount_cents, payee_ref, refund_request_id, "
                       "metadata_json FROM refunds WHERE state = 'succeeded' ORDER BY seq "
                       "LIMIT 1").fetchone()
    now = time.time()
    conn.execute("INSERT INTO refunds(id, seq, charge_id, amount_cents, payee_ref, "
                 "idempotency_key, refund_request_id, metadata_json, state, planned, "
                 "settle_after, created_at) VALUES ('re_syn_planted', 999, ?, ?, ?, "
                 "'planted-key', ?, ?, 'succeeded', 'succeeded', ?, ?)", (*row, now, now))
    conn.execute("INSERT INTO ledger_events(refund_id, from_state, to_state, reason, at) VALUES "
                 "('re_syn_planted', NULL, 'pending', 'planted', ?)", (now,))
    conn.execute("INSERT INTO ledger_events(refund_id, from_state, to_state, reason, at) VALUES "
                 "('re_syn_planted', 'pending', 'succeeded', 'planted', ?)", (now,))
    conn.commit()
    conn.close()
    p = subprocess.run([sys.executable, str(ORACLE), "--db", str(db), "--ledger", str(led),
                        "--out", str(d / "oracle.json")], capture_output=True, text=True,
                       timeout=60)
    res = json.loads((d / "oracle.json").read_text(encoding="utf-8"))
    clean = subprocess.run([sys.executable, str(ORACLE), "--db", str(db_src), "--ledger",
                            str(led_src)], capture_output=True, text=True, timeout=60)
    ok = p.returncode == 1 and any(v["invariant"] == "I1_single_success"
                                   for v in res["violations"]) and clean.returncode == 0
    return {"scenario": "oracle_negative_control", "ok": ok,
            "planted_verdict": res["verdict"], "planted_rc": p.returncode,
            "violations": res["violations"], "unplanted_rc": clean.returncode}


def reset_determinism(runtime: Path, seed: int) -> dict[str, Any]:
    a = Slice(runtime, seed=seed).reset()
    b = Slice(runtime, seed=seed).reset()
    keys = ("slice_db", "ledger")
    return {"scenario": "reset_deterministic", "ok": all(a[k] == b[k] for k in keys),
            "first": {k: a[k] for k in keys}, "second": {k: b[k] for k in keys}}


def busy_port_up(runtime: Path, seed: int) -> dict[str, Any]:
    sl = Slice(runtime, seed=seed)
    sl.reset()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    err = None
    try:
        sl.up(pay_port=port)
    except SliceError as exc:
        err = str(exc)
    finally:
        s.close()
        sl.down()
    ok = err is not None and "cannot bind" in err and not any(sl.running().values())
    return {"scenario": "busy_port_up", "ok": ok, "error": (err or "")[:400]}


def cmd_smoke(a: argparse.Namespace) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    runtime = Path(a.runtime)
    names = a.only.split(",") if a.only else list(SCENARIOS)
    t0, c0 = time.monotonic(), _cpu()
    rows = []
    for n in names:
        if n == "reset_deterministic":
            r = reset_determinism(runtime, a.seed)
        elif n == "busy_port_up":
            r = busy_port_up(runtime, a.seed)
        else:
            r = run_scenario(n, out, runtime, a.seed)
        rows.append(r)
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {n:28s} "
              f"{r.get('wall_s', 0):6.2f}s  oracle={r.get('oracle', {}).get('verdict', '-')}",
              flush=True)
    if not a.only:
        for extra in (reset_determinism(runtime, a.seed), busy_port_up(runtime, a.seed)):
            rows.append(extra)
            print(f"{'PASS' if extra['ok'] else 'FAIL'}  {extra['scenario']:28s}", flush=True)
    src = out / "legal_approve" / "final_state"
    if "legal_approve" in names and (src / "ledger.sqlite").exists():
        nc = oracle_negative_control(out, src)
        rows.append(nc)
        print(f"{'PASS' if nc['ok'] else 'FAIL'}  oracle_negative_control      "
              f"planted={nc['planted_verdict']}", flush=True)
    matrix_ok = all(r["ok"] for r in rows if r["scenario"] in MATRIX)
    total = {"ok": all(r["ok"] for r in rows), "matrix": list(MATRIX), "matrix_ok": matrix_ok,
             "scenarios": rows, "seed": a.seed,
             "wall_s": round(time.monotonic() - t0, 2), "cpu_s": round(_cpu() - c0, 2),
             "python": sys.version.split()[0]}
    (out / "smoke.json").write_text(json.dumps(total, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
    print(f"smoke: {'PASS' if total['ok'] else 'FAIL'} wall={total['wall_s']}s "
          f"cpu={total['cpu_s']}s -> {out}")
    return 0 if total["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/shop_slice.py")
    ap.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reset")
    r.add_argument("--seed", type=int, default=7)
    u = sub.add_parser("up")
    u.add_argument("--pay-port", type=int, default=0)
    u.add_argument("--shop-port", type=int, default=0)
    sub.add_parser("down")
    sub.add_parser("status")
    s = sub.add_parser("smoke")
    s.add_argument("--out", required=True)
    s.add_argument("--only", default="")
    s.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    if a.cmd == "smoke":
        a.runtime = a.runtime if a.runtime != str(DEFAULT_RUNTIME) else \
            str(Path(DEFAULT_RUNTIME).parent / "shop_slice_smoke")
        return cmd_smoke(a)
    sl = Slice(a.runtime)
    if a.cmd == "reset":
        sl.seed = a.seed
        print(json.dumps(sl.reset(), indent=1))
        return 0
    if a.cmd == "down":
        sl.down()
        print(json.dumps({"running": sl.running()}))
        return 0
    if a.cmd == "status":
        print(json.dumps({"running": sl.running(), **{s: sl.ready(s) for s in
                                                       ("pay", "shop", "worker")}}, indent=1))
        return 0
    if not sl.db.exists():
        print(f"нет БД контура {sl.db}: сначала `reset`", file=sys.stderr)
        return 2
    try:
        info = sl.up(pay_port=a.pay_port, shop_port=a.shop_port)
    except SliceError as exc:
        print(f"up: FAIL: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(info, indent=1))
    return 0


if __name__ == "__main__":
    os.environ.pop("RESEARCH_LLM_API_KEY", None)
    sys.exit(main())
