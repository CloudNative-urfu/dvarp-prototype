## ADDED Requirements

### Requirement: Broker-authored attempt record
The broker SHALL build every `AttemptRecord` of a sandboxed attacker from its own copy of the channel (attempt number, episode and brief ids, relayed messages and replies, session status). The attacker process MUST contribute only a `self_report` with `tactics` (list of strings) and `believed_success` (boolean), labelled as self-reported in the record.

#### Scenario: Forged identifiers and turns are not accepted
- **WHEN** the attacker process returns a record with `episode_id=FORGED-EP`, `brief_id=FORGED-BRIEF`, `attempt=99` and a turn that never passed through the channel
- **THEN** the attempt ends with `status=protocol_violation` and `violation=ipc_protocol_error`, `ipc_errors` is non-empty, and none of the forged values appear in `records` or in the event log

#### Scenario: Tactic count must match broker turns
- **WHEN** `self_report.tactics` has a length different from the number of turns the broker relayed in that attempt
- **THEN** the attempt is recorded as `ipc_protocol_error`

#### Scenario: Sandbox and in-process records stay identical on the stub
- **WHEN** mode D (corpus v1, seeds 7, 8, 9) and mode A stub (at least 3 briefs × 3 attempts) run in sandbox and in-process
- **THEN** attempt records, transcripts and evaluator outcomes are byte-identical between the two paths

### Requirement: Malformed attacker messages are protocol violations
Any failure while decoding, validating or recording an `event` or `record` from the attacker process (including `IPCProtocolError`, `EventLogError`, `TypeError`, `KeyError`, `ValueError`) MUST be converted into an `ipc_protocol_error` of the current attempt and MUST NOT surface as a runner episode error. Attacker `attempt` events MUST carry `cost=null` and an `episode_id` equal to the open attempt; model cost MUST be written only by the broker's model client.

#### Scenario: Inflated cost is rejected
- **WHEN** the attacker process emits an `attempt` event with `cost.tokens_in=1000000000`
- **THEN** the attempt is `ipc_protocol_error` and no attacker cost from that event is recorded

#### Scenario: Event for another episode is rejected
- **WHEN** the attacker process emits an event whose `episode_id` differs from the open attempt
- **THEN** the attempt is `ipc_protocol_error` and the runner records no episode error

#### Scenario: Mutant reading child turns is detected
- **WHEN** a mutant broker builds the record from the child's turns again
- **THEN** the forged-record test fails
