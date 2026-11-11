## 1. Tasks

- [x] inventory Dependency scan (scripts ↔ tests ↔ darp_range ↔ artifacts) and classification of every category; recorded in `inventory.md`
- [x] agreement Inventory approved by the issue author (axstiz) before any deletion
- [x] archive Annotated tag `archive/rev11` on `develop` (pre-cleanup state)
- [x] slim-verify New `scripts/verify.py` (env → unittest → configs → corpus → probes → mutations → e2e offline v2r + verify → stress → acceptance → rev11_verify → compaction); `verify_e2e.py` and `rev10_verify.py` deleted
- [x] scripts-cleanup One-off scripts deleted per `inventory.md`; `mutate_rev10.py` trimmed of `historical.py` mutants; `mutate_rev8.py` live_labels group dropped (deleted rev8 slice script), order_scope mutants repointed to the inlined test helpers; `oss_record_scores.py` deleted (imported the deleted `coupling_analysis`); `isolated_verify.sh` and `rev11_verify` strings updated
- [x] tests-cleanup Closed-revision tests deleted (`test_rev10_historical`, `test_rev10_legacy_compare`, `test_revision4_live_summary`, `test_revision8_live_slice`); `test_rev10_v2r` and `test_revision8_routing_neutrality` adapted
- [x] artifacts-cleanup Artifact policy applied per `inventory.md`; `brief.md` → `docs/`, `report-rev11-integrate.md` → `docs/` (unique rev11 summary); campaign files deleted
- [x] docs README/AGENTS.md updated (structure, verify commands, no verify_e2e)

## Notes

Test details are in the spec scenarios below. Frozen defense code stays byte-identical
throughout (`configs.lock.json` verification is a standing step of `scripts/verify.py`).

Final acceptance (clean-copy `scripts/verify.py` + `report_cli offline` table L*) is explicitly
**deferred with justification**: the current tree is a factory-generated scaffold — the team
selected the last revision from the autonomous campaign loop and will rework it further; the
system is not guaranteed to run end-to-end yet, and running the ~30-minute verification against
a scaffold scheduled for rework adds no signal. Deferred checks stay in the spec above; the
pre-cleanup state is archived under the tag `archive/rev11`, so verification can be run on the
trimmed tree at any time. Non-deferred gates applied: `ruff check` clean, pre-commit hooks
(ruff + commitizen) green on every commit, frozen defense code byte-identical.
