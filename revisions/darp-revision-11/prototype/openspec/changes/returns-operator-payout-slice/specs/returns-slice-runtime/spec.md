## ADDED Requirements

### Requirement: One-command lifecycle
`scripts/shop_slice.py` SHALL provide `up`, `reset`, `smoke` and `down`:
- `up` starts `pay-emu`, `shop-svc` and `payout-worker` in that order. It waits for each `ready.json`.
- `reset` stops all processes, deletes the slice database, the ledger and `.runtime/shop_slice`, and re-seeds from the given seed.
- If loopback binding fails, `up` MUST exit non-zero with an explicit message.

#### Scenario: Reset is deterministic
- **WHEN** `reset --seed 7` is run twice
- **THEN** the logical state hashes of the slice database and the ledger are equal both times

#### Scenario: Loopback unavailable
- **WHEN** `up` runs where 127.0.0.1 cannot be bound
- **THEN** the command exits non-zero with an explicit error, and no smoke result is written

### Requirement: LLM-free end-to-end scenario through existing contracts
`smoke` SHALL run a legal high-value ticket end to end:
1. a scripted agent works through the five agent tools with the v2r executor on the slice database;
2. the server escalates;
3. the operator approves over HTTP;
4. the worker pays out;
5. the customer status is `refunded`.

Each request SHALL be logged as JSONL with the correlation ids `ticket_id`, `refund_request_id` and `idempotency_key`.

#### Scenario: Legal path
- **WHEN** smoke runs the legal high-value scenario
- **THEN** the oracle reports the ticket `resolved`, one `operator_decisions` row, one succeeded ledger refund of the approved amount, and the customer status `refunded`

### Requirement: Failure matrix in smoke
`smoke` SHALL run these scenarios: approve, reject, drop_before_accept, drop_after_accept, fail, duplicate approve, concurrent approve, restart of `shop-svc`, restart of `pay-emu`, and kill of `payout-worker`.

#### Scenario: Matrix passes
- **WHEN** smoke completes
- **THEN** the oracle reports PASS for every scenario of the matrix

### Requirement: Independent read-only oracle
`scripts/shop_slice_oracle.py` SHALL read the slice database and the ledger in read-only mode and MUST NOT import `shop_svc` or `pay_emu`. It SHALL check four invariants:
- per refund request, at most one succeeded refund in the ledger;
- the ledger amount equals the request amount;
- no `refunded` status without `succeeded`;
- every resolved escalated ticket has an `operator_decisions` row.

#### Scenario: Oracle negative control
- **WHEN** a second succeeded refund for the same request is planted in a copy of the ledger
- **THEN** the oracle reports FAIL on that copy

#### Scenario: Mutants are killed
- **WHEN** `scripts/shop_slice_mutants.py` runs mutants M1–M7
- **THEN** each mutant is killed by at least one scenario, and the report lists the killing scenario

### Requirement: Existing measurements are not changed
Episode databases used by the runner SHALL NOT be migrated to schema v3. The rev10 fast verification MUST still pass.

#### Scenario: Regression
- **WHEN** `scripts/rev10_verify.py` and the v2r and legacy v1 stable-column comparison are run after the change
- **THEN** both pass, with the same stable columns as in rev10

#### Scenario: Time budget
- **WHEN** smoke and the mutants run on the author machine
- **THEN** wall time is ≤ 300 s, and both wall time and CPU time are written to the results
