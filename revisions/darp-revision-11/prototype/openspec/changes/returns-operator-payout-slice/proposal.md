## Why

On the recommended v2r foundation a refund that needs a human ends in a dead end. `executor._route_review` writes `manual_reviews` and sets the ticket to `escalated`. No operation lets an operator finish the case. Operator approvals and verified defects come from an episode fixture written before the session (`state_store/trusted.record_trusted`), and the operator role there is an unauthenticated string. A payout does not exist: `refund_requests.status` is always `created`.

As a result, the process owner and the manual-review lead cannot observe the outcomes that matter for money: a double payout after a timeout, a payment with unknown outcome, a payee substitution, or an "approval" claimed by the customer in chat. Infrastructure section I of the coevolution programme did not advance in rev9–rev10. Two residuals from the rev10 critique stay open: the broker has no IPC timeout, and `Runner`/`report_cli` live mode A silently runs in-process.

## What Changes

- New local process `shop-svc` (HTTP on 127.0.0.1) over a dedicated slice database (schema v3 migration only for this database). It exposes authenticated operator `approve`/`reject` for escalated v2r tickets. The server re-checks R1, R3 and R4, ownership, and the ticket state in one `BEGIN IMMEDIATE` transaction. Decisions go to an append-only `operator_decisions` table.
- New local process `pay-emu` with its own SQLite ledger. It holds synthetic charges and refunds with a required `Idempotency-Key`, the states pending → succeeded | failed, a payee fixed by the charge, and a refund total capped by the charge amount. It also supports evaluator-owned fault injection: drop before accept, drop after accept, settle delay, and failure.
- Payout outbox (`payouts`) in `shop-svc` with a separate `payout-worker` process. It uses a stable idempotency key per refund request, an `unknown` state for unknown outcomes, reconciliation by status lookup, and monotonic transitions. It adds a read-only customer status endpoint.
- `scripts/shop_slice.py` with up/reset/smoke/down commands, an LLM-free end-to-end scenario through the five agent tools, a failure matrix, an independent oracle that reads the ledger and the slice database directly, and mutants M1–M7.
- Broker fail-closed: a timeout on every wait for the attacker process, bounded stderr, and `Runner` live mode A refusing to run without an attacker factory unless the in-process research mode is explicit.
- Not changed: the five agent tools (`refund-tools-1`), the episode databases and logical hashes used by verify, the guardrail configurations, and the L* computations.

## Capabilities

### New Capabilities
- `operator-decision`: authenticated operator approve/reject of escalated tickets with server-side invariant re-check.
- `payment-emulator`: stateful local payment emulator with idempotency, fixed payee, and evaluator-owned fault injection.
- `payout-reconciliation`: payout outbox, retries with a stable key, reconciliation, and customer-visible status.
- `returns-slice-runtime`: one-command lifecycle, end-to-end smoke matrix, independent oracle, and regression guard.
- `broker-fail-closed`: IPC timeout, bounded stderr, and fail-closed live mode A without a sandbox factory.

### Modified Capabilities
<!-- none: existing executor, tools and measurement contracts are unchanged -->

## Impact

- New packages `darp_range/shop_svc`, `darp_range/pay_emu`; new scripts `scripts/shop_slice.py`, `scripts/shop_slice_oracle.py`, `scripts/shop_slice_mutants.py`; new tests.
- `darp_range/isolation/sandbox.py`, `darp_range/runner` (preflight), and the `report_cli` live path change for broker fail-closed.
- Runtime files go only under `.runtime/`. The operator token is a synthetic test-slice credential, not a secret.
- 0 Qwen requests. The new fast checks must take ≤ 300 s wall time, and both wall time and CPU time are reported.
