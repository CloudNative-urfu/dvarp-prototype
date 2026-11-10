## ADDED Requirements

### Requirement: Closed-revision checks are historical
For a closed revision N, verify SHALL check the sha256 of N's preregistration, the sha256 of artifacts recorded by N's runs, and N's behavioural checks (replay, table recomputation, mutants). Drift of current code from N's frozen hashes MUST be reported in a `drift` field and MUST NOT fail verify, unless N has no behavioural check, in which case the hash check keeps failing and N is listed.

#### Scenario: Comment change in shared code
- **WHEN** a comment line is changed in `darp_range/runner/flow.py` in a clean copy
- **THEN** no closed-revision check fails and the file appears in `drift`

#### Scenario: Tampered preregistration
- **WHEN** one byte of `artifacts/preregistration-rev8.json` is changed
- **THEN** `revision8_checks` fails

#### Scenario: Behavioural regression still caught
- **WHEN** a mutant changes the outcome of a rev7 table row or a rev8 cassette replay
- **THEN** the corresponding behavioural check fails

### Requirement: No cross-revision hash deviations
Revision N+k MUST NOT write hash-update deviations for a closed revision N.

#### Scenario: No new deviations in revision 10
- **WHEN** revision 10 changes `darp_range/isolation/sandbox.py` and `darp_range/runner/flow.py`
- **THEN** no `file_hash_updates` entry for an earlier preregistration is created and verify passes

### Requirement: Per-revision budget files
Each revision SHALL record live-request accounting in `artifacts/revision-N-budget.json` created at the start of the pass; checks of revision N MUST read only that file, and the shared `improvement-budget.json` MUST NOT be the source for any closed-revision check.

#### Scenario: Rev9 budget unaffected by rev10
- **WHEN** revision 10 writes its own budget file
- **THEN** `revision9_checks` reads `revision-9-budget.json` and passes without a new deviation

### Requirement: Fast change check
A command `scripts/rev10_verify.py` SHALL run this change's checks offline and report its duration; target is at most 10 minutes.

#### Scenario: Fast check output
- **WHEN** the command runs without network and without `RESEARCH_LLM_*`
- **THEN** it writes a summary with pass/fail per task id and the elapsed seconds
