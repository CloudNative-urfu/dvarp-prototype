## ADDED Requirements

### Requirement: Paths are launched from a server-side whitelist
The console SHALL launch only the seven defined paths, building argv on the server from the path definition and validated parameters. Unknown paths, unknown configuration names, malformed seed lists and run directory names that already exist MUST be rejected with a field-level error and no process started.

#### Scenario: Unknown configuration
- **WHEN** an offline run is requested with configurations `g0,bogus`
- **THEN** the request fails with "Нет таких конфигураций: bogus" and no job is created

#### Scenario: Injection attempt in seeds
- **WHEN** seeds are `7;rm`
- **THEN** the request fails with a seeds validation error

### Requirement: Job output is streamed and kept
A launched job SHALL stream stdout and stderr to the browser as it is produced, show PASS/FAIL lines as a checklist, and keep the command, full output, exit code and status so the job can be reopened after a console restart. A running job SHALL be stoppable.

#### Scenario: Shop smoke from the console
- **WHEN** the shop contour path is launched
- **THEN** scenario lines appear while the process runs and the job ends with status `ok` and a link to its results

### Requirement: Local only, no stored key
The console server SHALL bind to 127.0.0.1. Model settings SHALL be stored outside git and the API key SHALL NOT be stored; offline jobs SHALL run without `RESEARCH_LLM_*` variables.

#### Scenario: Offline job environment
- **WHEN** an offline run starts while model settings are saved
- **THEN** its process environment has no `RESEARCH_LLM_*` variables

### Requirement: Run results are readable without the terminal
For a finished run the console SHALL show per-configuration metrics with break-even status and Pareto membership, self-checks, the live budget for live runs, and attacker dialogs with the evaluator verdict and the attacker's own perception of each attempt.

#### Scenario: Live mode A dialogs
- **WHEN** a live mode A run is opened on the dialogs tab
- **THEN** each attack attempt shows its messages, tactics, the evaluator verdict from the database and whether the attacker believed it succeeded
