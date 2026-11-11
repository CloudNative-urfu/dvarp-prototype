## ADDED Requirements

### Requirement: Clean-copy verification
The repository SHALL be fully verifiable from a clean Git copy by
`python3 scripts/verify.py`: environment probe, full unittest suite, frozen-config and corpus
verification, component probes, mutation suite, `report_cli offline` (foundation v2r, seeds
7,8,9) with `verify`, a stress run (seed 7), acceptance of key requirements, `rev11_verify`
(`--skip-rev10`), and raw-run compaction. The old `verify_e2e.py` and `rev10_verify.py` SHALL
be deleted.

#### Scenario: Clean copy verifies
- **WHEN** the repository is cloned fresh and `python3 scripts/verify.py --out runs/verify` runs
- **THEN** it exits 0 with all steps PASS and writes `runs/verify/verify.json`

#### Scenario: Offline L* from a clean copy
- **WHEN** `python3 -m darp_range.report_cli offline --out <tmp> --seeds 7` runs in a fresh clone
- **THEN** the report contains the L* table with price bounds

### Requirement: One-off tooling removed
Scripts and tests that exist only to validate closed revisions (1–9) or campaign bookkeeping
SHALL be removed; the standing mutation suite (`mutate_*.py`), live drivers, corpus
generators, passports, compaction and isolation tooling SHALL stay. The decision per file
SHALL be recorded in the change inventory before deletion.

#### Scenario: No references to deleted files
- **WHEN** the removal is applied
- **THEN** no remaining script, test or module imports or executes a deleted file (except
  historical docstring mentions in frozen defense code and archived reports)

### Requirement: Significant artifacts preserved
Preregistrations (`artifacts/preregistration-*.json`), `artifacts/attack-cards.json`,
`artifacts/report.md` and `artifacts/report-history.md` SHALL be kept. Per-revision working
copies (plan/budget/results), campaign status bookkeeping and `simplifications.json` SHALL be
removed; the pre-cleanup state SHALL be archived under the annotated tag `archive/rev11`.

#### Scenario: Preregistrations survive cleanup
- **WHEN** the cleanup is applied
- **THEN** every `artifacts/preregistration-*.json` present before it is still present

### Requirement: Frozen defense code untouched
The cleanup SHALL NOT modify any file inside the `DEFENSE_CODE` hash tree
(`darp_range/{configs,guard_contract,guard_plugins,guard_policy,simple_baseline,executor,tools}/`);
`configs.lock.json` verification SHALL keep passing.

#### Scenario: Freeze intact
- **WHEN** `python3 -m darp_range.configs verify --dir configs` runs after the cleanup
- **THEN** no drift is reported
