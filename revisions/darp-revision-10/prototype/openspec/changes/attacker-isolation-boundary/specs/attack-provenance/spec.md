## ADDED Requirements

### Requirement: Briefs carry provenance from an evaluator registry
Every attacker brief SHALL carry a provenance value from {public, implementation-informed, blind-critic, research-feedback} taken from a registry held by the evaluator. All existing corpora (v1, holdout_v2, heldout_v3, trusted_v7) MUST be registered as implementation-informed without changing corpus text or MANIFEST hashes.

#### Scenario: Legacy corpus is implementation-informed
- **WHEN** a brief is built from any existing corpus entry
- **THEN** its provenance is `implementation-informed` and the corpus MANIFEST hashes are unchanged

#### Scenario: Forged provenance is rejected
- **WHEN** a brief declares `blind-critic` but its entry hash is absent from the evaluator registry
- **THEN** the run refuses the brief with an explicit provenance error

### Requirement: Run claim level is computed from facts
The run manifest SHALL contain `claim_level` computed as `isolated_black_box_candidate` only if the attacker ran in the sandbox, all OS negative probes of this run passed, research-mode was off, and no brief is implementation-informed; otherwise `isolated_implementation_informed` (sandboxed with legacy briefs) or `in_process_research`.

#### Scenario: Current corpora in the sandbox
- **WHEN** a sandboxed stub run uses corpus v1 briefs with all probes denied
- **THEN** `claim_level` is `isolated_implementation_informed`

#### Scenario: Research mode never black-box
- **WHEN** a sandboxed run with only registered blind briefs runs in research-mode
- **THEN** `claim_level` is not `isolated_black_box_candidate`

#### Scenario: Probe failure downgrades
- **WHEN** any negative OS probe of the run reports PASS
- **THEN** `claim_level` is `in_process_research`
