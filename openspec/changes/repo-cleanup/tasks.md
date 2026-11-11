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

Test details are in the spec scenarios below. Acceptance after deletion: full unittest from a
clean copy, `scripts/verify.py` from a clean copy (PASS), `report_cli offline` table L* from a
clean copy. Frozen defense code stays byte-identical throughout.
