## ADDED Requirements

### Requirement: Payout outbox with stable idempotency key
Every refund request in the slice database SHALL get one `payouts` row (`refund_request_id` UNIQUE) in the same transaction. The row's `idempotency_key` is `rr-<refund_request_id>`. A separate `payout-worker` process SHALL send payouts to `pay-emu` and MUST reuse the same key on every retry.

#### Scenario: Normal payout
- **WHEN** a refund request is created and the worker runs with no faults
- **THEN** `payouts.state` becomes `succeeded`, and the ledger has exactly one `succeeded` refund whose amount equals `refund_requests.amount_cents`

#### Scenario: Retry after drop before accept
- **WHEN** the first POST is dropped before acceptance
- **THEN** the worker retries with the same key and the ledger has exactly one refund for the request

#### Scenario: Worker killed mid-flight
- **WHEN** `payout-worker` is SIGKILLed after sending the POST and before storing the response, then restarted
- **THEN** the ledger has exactly one refund for the request, and `payouts.state` reaches `succeeded`

#### Scenario: Mutant generates a new key per retry
- **WHEN** mutant M3 uses a fresh key on each retry
- **THEN** the "worker killed mid-flight" or "drop after accept" scenario shows two refunds and fails

### Requirement: Unknown outcome is not success and transitions are monotonic
A connection error or timeout SHALL set `payouts.state='unknown'`. Reconciliation by `GET /v1/refunds?idempotency_key=` SHALL map the result to `pending`, `succeeded` or `failed`, or back to `requested` if the emulator has no refund for the key. Terminal states `succeeded` and `failed` MUST NOT be overwritten.

#### Scenario: Drop after accept is reconciled
- **WHEN** the POST is dropped after acceptance
- **THEN** `payouts.state` is `unknown` until reconciliation, then `succeeded`, and the ledger has exactly one refund

#### Scenario: Stale pending after success
- **WHEN** a reconciliation result `pending` arrives after the row is `succeeded`
- **THEN** the row stays `succeeded`

#### Scenario: Mutant non-monotonic reconciliation
- **WHEN** mutant M7 lets a stale `pending` overwrite `succeeded`
- **THEN** the "stale pending after success" test fails

#### Scenario: Payment failure
- **WHEN** the fault file sets `fail(declined)` for the key
- **THEN** `payouts.state` is `failed`, and the worker sends no further POST for that request

### Requirement: Customer-visible status from trusted state
`GET /v1/customer/tickets/{id}` SHALL require the customer token of the ticket owner and return one of `under_review`, `approved_payment_pending`, `refunded`, `refused`, `payment_issue`. `refunded` MUST be returned only when `payouts.state='succeeded'`. Another customer's token MUST get 404.

#### Scenario: Unknown is shown as pending
- **WHEN** a payout is `unknown`
- **THEN** the customer status is `approved_payment_pending`

#### Scenario: Mutant shows unknown as refunded
- **WHEN** mutant M6 maps `unknown` to `refunded`
- **THEN** the "unknown is shown as pending" test fails

#### Scenario: Foreign ticket
- **WHEN** customer A requests the status of customer B's ticket
- **THEN** the response is 404 and contains no data of the ticket
