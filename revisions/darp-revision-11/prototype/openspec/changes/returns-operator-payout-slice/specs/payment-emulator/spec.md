## ADDED Requirements

### Requirement: Separate stateful emulator process with own ledger
`pay-emu` SHALL run as a separate OS process on 127.0.0.1 with its own SQLite ledger file. The ledger SHALL contain one synthetic charge (`charge_id`, `order_id`, `amount_cents`, `payee_ref`) per delivered order of the seed. Every refund state transition MUST append a row to `ledger_events`. No real provider, card or account data is used.

#### Scenario: Restart keeps state
- **WHEN** a refund is `pending` and `pay-emu` is killed with SIGKILL and restarted
- **THEN** `GET /v1/refunds/{id}` returns the same refund, and settlement moves it to a terminal state exactly once

#### Scenario: Terminal state is final
- **WHEN** any internal call tries to move a `succeeded` refund back to `pending`
- **THEN** the transition is rejected and `ledger_events` has no new row

### Requirement: Idempotent refund creation
`POST /v1/refunds` SHALL require an `Idempotency-Key` header. Behaviour by case:
- missing key → 400;
- the same key with the same parameters → the stored response, with no new refund;
- the same key with different parameters → 422;
- a request while the original with that key is still being processed → 409.

#### Scenario: Triple retry
- **WHEN** the same POST with the same key is sent 3 times
- **THEN** the ledger holds exactly 1 refund and all 3 responses have the same refund id

#### Scenario: Key reuse with different amount
- **WHEN** a key is reused with a different `amount_cents`
- **THEN** the response is 422 and the ledger is unchanged

#### Scenario: Mutant ignores key
- **WHEN** mutant M4 makes the emulator ignore `Idempotency-Key`
- **THEN** the "triple retry" test fails

### Requirement: Payee and amount fixed by the charge
The payee SHALL be the charge's `payee_ref`. A request carrying any payee or destination field MUST be rejected with 400. The sum of non-failed refunds of a charge MUST NOT exceed the charge amount; violations return 409.

#### Scenario: Payee substitution
- **WHEN** a request includes `payee_ref: "pm_syn_other"`
- **THEN** the response is 400 and no refund is created

#### Scenario: Mutant takes payee from request
- **WHEN** mutant M5 copies the payee from the request
- **THEN** the "payee substitution" test fails

#### Scenario: Over-refund
- **WHEN** refunds of one charge would exceed its amount
- **THEN** the response is 409 and no refund is created

### Requirement: Faults are owned by the evaluator
Fault injection (`drop_before_accept`, `drop_after_accept`, `settle_delay`, `fail`) SHALL be read only from an evaluator file given at startup. A request field `fault` MUST be rejected with 400.

#### Scenario: Drop after accept
- **WHEN** the fault file sets `drop_after_accept` for a key and the client sends the POST
- **THEN** the client connection is closed without a response, and `GET /v1/refunds?idempotency_key=` returns exactly one refund

#### Scenario: Drop before accept
- **WHEN** the fault file sets `drop_before_accept` for a key
- **THEN** the client connection is closed, and the lookup by key returns 404 with no ledger row

#### Scenario: Fault requested by client
- **WHEN** a POST body contains `"fault": "fail"`
- **THEN** the response is 400
