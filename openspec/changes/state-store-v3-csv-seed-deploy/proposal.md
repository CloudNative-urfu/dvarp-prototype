# state-store v3, CSV seed and deploy manifest

## Why

The refund service database is being tuned as a product (team-lead decision
2026-11-14, issue #38). The design doc `docs/refund-db-design.md` (issue #37,
PR #41) fixed the target schema v3 and the "required vs present" gap list
(§8) with the defense-code edit list (§8.1). Without this change:

- the operator queue is not observable from the database: `manual_reviews`
  has only `enqueued_at`, so the canon metrics E(g)/Q(g) and
  `P(W_q > Q_SLA)` (dvarp#18) cannot be computed from the episode state;
- the ticket closing moment, clarification texts and per-operation payloads
  are reconstructible only from the out-of-database EventLog;
- shop data (catalog, benign notes) is hardwired in `generator.py`, so the
  deploy script promised for traffic-svc (#36) has no data source;
- the manifest binding legal episodes to real database rows does not exist,
  while #19 fixes: the episode invitation carries the snapshot id+hash and
  legal episodes bind to real order rows (no fictitious ids).

## What Changes

- Schema v3 migration (`state_store/migrate_v3.py`, modeled on
  `apply_schema_v2`): nullable audit columns `manual_reviews(decided_at,
  decision, decided_by)`, `info_requests.question_text`, `ops_ledger.
  detail_json`, `tickets.closed_at`; the `refund_requests.status` dictionary
  is extended with `approved | rejected | cancelled` (table rebuild, columns
  and constraints otherwise as v1); five indexes (design §3).
- Schema-only policy: values for the new columns and the new status outcomes
  are written by defense code later (defense-code list §8.1, implemented in
  #39); in this change only the schema appears.
- CSV seed (`state_store/seed_csv.py`, `state_store/seed/*.csv`) replaces
  only the data sources `CATALOG`/`_BENIGN_NOTES`; generator logic,
  determinism and seeds are unchanged; same seed + same CSV yield the same
  logical state hash.
- Deploy script (`state_store/deploy.py`, CLI `deploy`/`check-manifest`):
  build → migrations → mandatory `assert_synthetic` (incl. forbidden
  columns) → snapshot with hash → manifest.
- Traffic manifest (`state_store/manifest.py`): the binding core is built by
  the generator from the same rows and is part of `dataset_hash`
  (`generator.ShopData.manifest`), so the manifest and the snapshot cannot
  diverge by construction; `verify_manifest` additionally checks the logical
  hash and the existence/fields of every referenced row; consumers bind
  episodes to real rows only.
- Not changed: the five agent tools, defense executor/tools/configs, episode
  DB v1/v2 logical hashes, frozen corpus (bound to the DB only via
  archetypes, `corpus/schema.py:3`), parity contract #19 (episode DBs stay
  unmigrated by default; `schema_version=3` is an explicit opt-in).

## Capabilities

### New Capabilities
- `state-store-v3`: audit columns, status lifecycle dictionary, indexes,
  idempotent v3 migration of episode/base databases.

### Modified Capabilities
<!-- none: the deploy path extends `state_store` build without changing the
build contract; seed determinism and snapshot/hash checks keep their
behavior (same seed → same logical hash) -->

## Impact

- `darp_range/state_store/` only (outside defense code): new `migrate_v3.py`,
  `seed_csv.py`, `manifest.py`, `deploy.py`, `seed/*.csv`, `README.md`;
  edits in `generator.py` (manifest core, optional catalog/notes),
  `store.py` (`apply_schema_version`, `ShopSpec.manifest`),
  `__init__.py`/`__main__.py` (exports, CLI).
- New tests `tests/test_state_store_deploy.py`.
- Defense-code edit list (design §8.1, items 1–4) is attached to the PR;
  the writes are implemented in #39 (freeze is deferred until prototype
  finalization, issue #45); no defense file is edited in this change.
- Alignment with spec `svc-decomposition` (#19): episode DB
  snapshots regenerate only for explicitly deployed v3 databases.
