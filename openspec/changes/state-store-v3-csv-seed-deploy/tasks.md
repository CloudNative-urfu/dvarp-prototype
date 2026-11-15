# Tasks

- [x] state-store-v3 Migration `apply_schema_v3` idempotent with `user_version=3`: audit columns (`manual_reviews.decided_at/decision/decided_by`, `info_requests.question_text`, `ops_ledger.detail_json`, `tickets.closed_at`), extended `refund_requests.status` dictionary (rebuild, rows preserved), five indexes per design §3; v1/v2 hashes untouched
- [x] csv-seed `seed_csv.py` loads catalog/benign notes with validation; seed CSVs reproduce the built-in constants byte-for-byte (same seed → same state hash); generator accepts optional catalog/notes without changing defaults
- [x] deploy-script `deploy.py` + CLI `deploy`/`check-manifest`: build → migrations → mandatory `assert_synthetic` → snapshot with hash → manifest; deterministic (same seed → same logical hash and identical manifest bytes)
- [x] traffic-manifest Binding core built by the generator and included in `dataset_hash` (`ShopData.manifest`); manifest binds episodes to real rows (no fictitious ids, no placeholders); `verify_manifest` checks the hash and existence/fields of every referenced row; tamper and missing-row detection tested
- [x] parity-contract Episode DBs stay unmigrated by default (`schema_version=1`); v3 is opt-in; same seed → identical baseline/final hashes per schema version; snapshot restore with hash check unchanged
- [ ] frozen-sanction-list Unfreeze list (design §8.1, items 1–4) attached to the PR description; sanction requested from the owner separately; no frozen file edited
- [ ] pr-opened PR to `develop`, `Closes #38`, issue #38 board status updated

## Notes

Verification: `ruff check .`; unit tests `tests.test_state_store`,
`tests.test_state_store_deploy` and neighbors green; `scripts/dev_smoke.py`
green (256 tests). Frozen corpus not rewritten (bound via archetypes only).
