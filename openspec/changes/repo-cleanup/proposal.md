## Why

The repository grew inside an autonomous research campaign (agent-driven revisions 1–11 with
human-independent handoffs, staged status reports and preregistration bookkeeping). The campaign
is closed; the repository now serves the actual prototype: run cases, measure metrics at defined
points, compute L* at the end (issue #2). One-off revision scripts, revision-scoped tests, the
monolithic `verify_e2e.py` and the campaign wrapper are dead weight that obscures the measurement
core and cannot even be reproduced from a clean Git copy.

Key fact discovered during inventory: the full `verify_e2e.py` already fails from a clean Git
copy — most of its revision steps depend on artifacts that were never exported to Git
(`artifacts/checks/revision{4,5,7,8}/...`, `artifacts/rev10/...`, `artifacts/history_rev10/...`,
~18 references). `scripts/rev10_verify.py` shares the same fate. The standing contracts they
check (v2r architecture, broker record, isolation) are covered by tests that do work from a
clean copy.

## What Changes

- New slim verifier `scripts/verify.py` (~200 lines, modeled on `rev11_verify.py`): environment
  probe → full unittest → frozen configs → corpus verify → component probes → mutation tests →
  `report_cli offline` v2r (seeds 7,8,9) + `verify` → stress run (seed 7) → acceptance of key
  requirements → `rev11_verify` (current slice, `--skip-rev10`) → raw-run compaction.
  The monolithic `verify_e2e.py` (1362 lines) and `rev10_verify.py` are deleted.
- ~47 one-off scripts deleted (revision 2–9 analytics, preregistration generators, campaign
  status collectors, `historical.py`, `validate_decomposition.py`). Standing tools stay
  (mutations, live drivers, corpus generators, passports, compaction, isolation, shop-slice).
- All `mutate_*.py` kept: they are the standing RED/GREEN mutation suite against current code.
  `mutate_rev10.py` is trimmed of mutants that patch the deleted `historical.py`.
- Tests: revision-scoped tests that verify closed-revision results are deleted
  (`test_rev10_historical`, `test_rev10_legacy_compare`, `test_revision4_live_summary`,
  `test_revision8_live_slice`). Tests of standing contracts keep their coverage; two of them
  are adapted to the removed scripts (`test_rev10_v2r`, `test_revision8_routing_neutrality`).
- Artifacts: preregistrations, `attack-cards.json`, `report.md`, `report-history.md` stay;
  per-revision `plan/budget/results` copies, campaign status/improvement bookkeeping,
  `simplifications.json`, `decomposition.json` and the decomposition framework
  (`workflow-contract.json`, `validate_decomposition.py`, `test_decomposition.py`,
  `handoff.json`, `progress.md`) are deleted. `brief.md` moves to `docs/`.
- Archive before deletion: tag `archive/rev11` on the pre-cleanup state of `develop`
  (the full snapshot is additionally preserved in `revisions/darp-revision-11/prototype/`).
- README and AGENTS.md updated for the trimmed layout (verify commands, structure table).

## Capabilities

### New Capabilities
- `repo-cleanup`: slim verification, one-off tooling removal, artifact policy, archive.

### Modified Capabilities
<!-- none: prototype behavior and measurement core unchanged -->

## Impact

- Deleted: ~47 scripts, 5 test files, ~40 artifact files, 3 campaign files; added: `scripts/verify.py`.
- `scripts/verify.py` becomes the full offline verification (~25–35 min instead of ~53).
- No changes to `darp_range/` behavior except docstring updates where deleted scripts were
  referenced; frozen defense code stays byte-identical (`configs.lock.json` untouched).
- Local developer workflow: `python3 scripts/verify.py --out runs/verify`;
  `python3 -m darp_range.report_cli offline` unchanged.
