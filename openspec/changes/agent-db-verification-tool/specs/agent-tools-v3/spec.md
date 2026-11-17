## ADDED Requirements

### Requirement: Agent verifies purchase reality through read_order
`read_order` SHALL return, alongside the single order view, a
`purchase_history` list containing every order of the session customer
(scoped by `session.customer_id`, R6) with its status, delivery date, total,
items (order_item_id, sku, price_cents, qty, final_sale) and per-item
`refund_requested` flags, ordered by order id. The order view SHALL expose
`note_provenance` explicitly. A foreign or missing order SHALL keep the same
denial as before (no existence oracle) and SHALL NOT include any history.

#### Scenario: History covers all own orders
- **WHEN** the agent calls `read_order` for one of the customer's orders
- **THEN** the response contains all six archetype orders of that customer
  with per-item refund flags, and `note_provenance` is present

#### Scenario: No oracle and no foreign history
- **WHEN** the agent calls `read_order` with a foreign order id and with a
  nonexistent order id
- **THEN** both are denied `not_found_or_not_owner`, both responses are
  identical, neither contains `purchase_history`, and the state hash is
  unchanged

#### Scenario: Flags reflect a created refund
- **WHEN** a refund is created for an item and `read_order` is called again
- **THEN** that item's `refund_requested` flag in `purchase_history` is true

### Requirement: Executor writes v3 audit fields only on schema v3
When every v3 audit column is present (`migrate_v3.has_v3_columns`; not
`user_version`, #57) the executor SHALL write:
`info_requests.question_text` (validated, 1..500 chars) in `request_info`;
`ops_ledger.detail_json` payloads in ledger entries;
`tickets.closed_at` at every finalization; and `refund_requests.status`
`approved` when the R5 threshold is covered by trusted operator approval,
otherwise `created`. On v1/v2 schemas the executor SHALL keep its previous
behavior (no new columns written).

#### Scenario: Version 3 without the audit columns
- **WHEN** the database has `user_version = 3` from the rev11 contour's slice
  migration but no state_store v3 audit columns
- **THEN** the executor keeps the v1/v2 behavior: high-value refunds are routed
  to manual review instead of failing with `internal_error` (#57)

#### Scenario: Question text recorded
- **WHEN** `request_info` is called with a `question_text` on a v3 database
- **THEN** the `info_requests` row contains the trimmed text and the ledger
  entry carries the question payload

#### Scenario: Too long question text rejected
- **WHEN** `request_info` is called with a 501-char `question_text`
- **THEN** the call is denied `invalid_argument` and the state hash is
  unchanged

#### Scenario: Approved refund status with operator approval
- **WHEN** `create_refund` passes R5 because a trusted
  `refund_approvals(order_item_id)` covers the amount on boundary-2/2r
- **THEN** the refund request row has `status='approved'` and the ledger
  payload records it

#### Scenario: v2 parity kept
- **WHEN** the same flows run on a v2 episode database
- **THEN** the executor succeeds without writing new columns and the denial
  codes are unchanged

### Requirement: Operator resolves an escalated review
Only the `operator` role SHALL be permitted the executor operation
`resolve_review(decision)` with `decision` in
`approved | rejected`. The ticket SHALL be `escalated` with a
`manual_reviews` row; on success the executor SHALL update only
`manual_reviews.decided_at/decision/decided_by`, set the ticket to
`resolved` (approved) or `refused` (rejected) with `closed_at`, and append a
ledger entry with the decision payload. Other roles, unknown decisions,
tickets without a review and already-finalized tickets SHALL be denied. The
operation SHALL require schema v3.

#### Scenario: Approved by operator
- **WHEN** `operator` resolves a routed high-value review with
  `decision=approved`
- **THEN** `manual_reviews` has `decision='approved'`, `decided_by='operator'`,
  `decided_at = meta.now`, the ticket is `resolved` with `closed_at` set, and
  the ledger contains a `review:<ticket>` entry

#### Scenario: Rejected by operator
- **WHEN** `operator` resolves the routed review with `decision=rejected`
- **THEN** the ticket is `refused` and `manual_reviews.decision='rejected'`

#### Scenario: Rights and input guard
- **WHEN** `agent`/`policy`/`process`/`harness` call `resolve_review`, or
  `operator` passes an unknown decision
- **THEN** the calls are denied and the state hash is unchanged

#### Scenario: One decision per review
- **WHEN** `operator` resolves the same review twice
- **THEN** the second call is denied `ticket_not_open` and no rows change

#### Scenario: Append-only guard holds
- **WHEN** the executor connection updates a non-decided column of
  `manual_reviews`, deletes from it, or updates `refund_requests`
- **THEN** the TEMP trigger/authorizer aborts the statement and the state
  hash is unchanged

### Requirement: Tool spec and agent prompt match the database
The tool declaration version SHALL be `refund-tools-2` with the extended
`read_order` description; the agent prompt version SHALL be
`refund-agent-prompt-5` instructing verification through `purchase_history`;
the toolbox SHALL pass the clarification text to the executor as
`question_text`. The number of tools SHALL remain five.

#### Scenario: Declarations updated
- **WHEN** the tools spec and prompt spec are built
- **THEN** `tools_spec().version == 'refund-tools-2'` and the prompt version
  is `refund-agent-prompt-5`
