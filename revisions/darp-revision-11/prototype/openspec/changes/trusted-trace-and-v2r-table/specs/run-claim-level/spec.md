## ADDED Requirements

### Requirement: Every run manifest carries a claim level
Every `manifest.json` written by the runner, report_cli and live scripts SHALL contain `claim_level` together with its facts (sandbox kinds, probe verdict, research mode, provenance labels, IPC errors). A run without an attacker factory MUST get `in_process_research`. A manifest of the new schema without `claim_level` MUST fail `load_manifest`.

#### Scenario: Offline in-process run
- **WHEN** `report_cli offline` runs without an attacker factory
- **THEN** each seed manifest contains `claim_level=in_process_research`

#### Scenario: Sandboxed run on legacy corpus
- **WHEN** a mode-D run on corpus v1 uses `AttackerFactory` with bwrap and passing broker probes
- **THEN** its manifest contains `claim_level=isolated_implementation_informed`

#### Scenario: Missing field is rejected
- **WHEN** `claim_level` is deleted from a schema-2 manifest
- **THEN** `load_manifest` raises a reproducibility error

### Requirement: Live adaptive attacker defaults to sandbox
A live mode-A run SHALL use the bwrap attacker factory by default. Running the attacker in-process MUST require an explicit `--in-process-research` flag, and without bwrap and without the flag the run MUST stop before the first model request.

#### Scenario: No sandbox, no flag
- **WHEN** live mode A starts on a host where bwrap is unavailable and the flag is absent, using a fake transport
- **THEN** the run exits with an explicit error and the fake transport receives 0 requests

### Requirement: Access matrix states enforcement
Each row of `configs/isolation/access_matrix.json` MUST have `enforcement` equal to `os` or `convention`. Every `deny` row with `enforcement=os` MUST reference a probe of its own with its own canary. The row defender→attacker_memory MUST be `convention`, and a defender-side gc probe searching for the attacker's brief, AttemptRecords and payload audit MUST be recorded as a known leak.

#### Scenario: OS-deny row without own probe
- **WHEN** a `deny` row with `enforcement=os` references another row's probe or none
- **THEN** the matrix validator fails

#### Scenario: Defender sees attacker is recorded, not hidden
- **WHEN** the defender-side gc probe runs on the current single-process defender
- **THEN** it finds the attacker canary and the result is stored as an expected leak referenced by the convention row
