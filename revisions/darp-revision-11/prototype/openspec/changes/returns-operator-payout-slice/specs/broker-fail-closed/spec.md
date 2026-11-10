## ADDED Requirements

### Requirement: Broker waits are bounded
Every broker wait for a message from the attacker process SHALL be bounded by the per-attempt limit. On expiry the broker MUST kill the attacker process group and record the attempt as `protocol_violation` with error `ipc_protocol_error`. The run MUST continue with the next attempt.

#### Scenario: Hanging child
- **WHEN** a hostile child stops answering after receiving an observation
- **THEN** the attempt is recorded as `ipc_protocol_error` within the limit + 5 s, and the run proceeds

#### Scenario: Mutant without timeout
- **WHEN** mutant M8 restores the unbounded `readline`
- **THEN** the "hanging child" test fails by its own deadline

### Requirement: Bounded stderr capture
The broker SHALL keep at most 64 KiB of attacker stderr and count dropped bytes.

#### Scenario: Stderr flood
- **WHEN** the child writes 64 MiB to stderr before `hello`
- **THEN** broker RSS grows by at most 16 MiB, and the attempt record contains the dropped-byte count

### Requirement: Live mode A is fail-closed without a sandbox
`Runner` with `attack_strategy=llm` and `agent_mode=live` SHALL raise an error before any model request, unless an attacker factory is given or `in_process_research=True` is set explicitly. `report_cli live` with attack mode A SHALL use the bwrap factory by default.

#### Scenario: No factory
- **WHEN** a live mode A runner without a factory starts on a fake transport
- **THEN** it raises an error, and the transport log has 0 requests

#### Scenario: Explicit research mode
- **WHEN** the same runner starts with `in_process_research=True`
- **THEN** it runs, and the manifest has `claim_level=in_process_research`

#### Scenario: Mutant silent fallback
- **WHEN** mutant M9 makes the runner fall back to in-process silently
- **THEN** the "no factory" test fails
