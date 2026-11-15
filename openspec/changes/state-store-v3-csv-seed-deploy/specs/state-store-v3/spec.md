## ADDED Requirements

### Requirement: Schema v3 migration is idempotent and additive
`state_store.migrate_v3.apply_schema_v3` SHALL add only nullable columns
(`manual_reviews.decided_at`, `manual_reviews.decision`,
`manual_reviews.decided_by`, `info_requests.question_text`,
`ops_ledger.detail_json`, `tickets.closed_at`), the five indexes of design §3
and the extended `refund_requests.status` dictionary
(`created|paid_synthetic|approved|rejected|cancelled`), set `user_version=3`
and the `meta.schema_version` label. Repeated application SHALL leave the
logical state hash unchanged. Episode databases v1/v2 SHALL keep their
logical hashes.

#### Scenario: Columns, indexes and version label appear
- **WHEN** `apply_schema_v3` runs on a v1 database built by seed 7
- **THEN** all new columns and the five indexes exist, `user_version=3`,
  `meta.schema_version='3'`, and the preserved `refund_requests` row count
  and amount sum equal the pre-migration values

#### Scenario: Repeated migration does not change the hash
- **WHEN** `apply_schema_v3` runs twice on the same connection
- **THEN** the second call changes nothing and the logical hash stays equal

#### Scenario: Unknown status rejected, new outcomes accepted
- **WHEN** a row with `status='approved'` is inserted and a row with
  `status='bogus'` is attempted after migration
- **THEN** `approved` is accepted by the CHECK and `bogus` raises an
  integrity error

### Requirement: CSV seed replaces only data sources
`state_store.seed_csv` SHALL load a catalog (`sku,title,category,
price_cents,final_sale`) and benign order notes from CSV with validation
(unique ascii skus, integer cents > 0, final_sale in {0,1}); the generator
SHALL accept an optional catalog/notes without changing its default
behavior, determinism or seed handling.

#### Scenario: Seed CSV reproduces the built-in dataset
- **WHEN** the deploy runs with `seed/catalog.csv` and
  `seed/benign_notes.csv` on seed 7
- **THEN** the state hash and dataset hash equal the ones built from the
  built-in constants

#### Scenario: Invalid seed rejected
- **WHEN** the catalog CSV has a duplicate sku, a non-positive price or a
  wrong header
- **THEN** loading raises `SeedError` and no database is built

### Requirement: Deploy script is deterministic and synthetic-safe
`state_store.deploy.deploy_database` (CLI `deploy`) SHALL build the database
from the seed, apply migrations up to the requested schema version, run the
mandatory `assert_synthetic` check (forbidden column fragments included),
take a verified snapshot and write the manifest. Same seed, parameters and
CSV SHALL produce the same logical state hash and an identical manifest
file.

#### Scenario: Same seed → same hashes
- **WHEN** two deploys run with the same seed and inputs
- **THEN** the printed `state_hash`/`dataset_hash` values are equal and the
  manifest files are byte-identical

#### Scenario: Deployed database is synthetic
- **WHEN** a v3 database is deployed
- **THEN** `assert_synthetic` passes on the final schema (audit columns
  included)

### Requirement: Manifest binds episodes to real rows and is part of the dataset hash
The binding core (`accounts` and `orders` with real `order_id`s,
`order_item_id`s, owners, archetypes) SHALL be built by the generator from
the same rows and included in `dataset_hash`
(`generator.ShopData.manifest`). The manifest file SHALL carry the dataset
hash, the state hash and the model `now`. `verify_manifest` SHALL check the
logical state hash and the existence and fields of every referenced row.
Fictitious ids and placeholders are excluded by construction.

#### Scenario: Manifest included in dataset hash
- **WHEN** the dataset hash is computed
- **THEN** it differs from the hash computed without the manifest, and the
  manifest file's `dataset_hash` equals the deploy output's `dataset_hash`

#### Scenario: Binding uses real rows only
- **WHEN** the manifest is loaded from a deploy
- **THEN** every `order_id` matches `^O-\d+$`, every `customer_id` matches
  `^C-\d+$`, every item id is an integer, and every account order reference
  resolves to a manifest order

#### Scenario: Tampered database rejected
- **WHEN** a row referenced by the manifest is modified or removed after
  deploy
- **THEN** `verify_manifest` raises `ManifestError`

#### Scenario: Parity contract kept
- **WHEN** episode databases are managed by `EpisodeStateManager` without an
  explicit schema request
- **THEN** the default remains v1 with the same baseline hash for the same
  seed, snapshot restore keeps its hash check, and v3 applies only when
  `schema_version=3` is requested explicitly
