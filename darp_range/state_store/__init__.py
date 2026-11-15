"""Компонент `state_store`: синтетическая БД магазина (SQLite), детерминированный
генератор данных, снимок и полное восстановление состояния перед эпизодом
(FR-1.10, NFR-2.1, NFR-2.13).

Только стандартная библиотека. CLI: `python3 -m darp_range.state_store --help`.
"""

from .fixtures import FixtureError, apply_fixture
from .generator import (
    ARCHETYPES, CATALOG, DEFAULT_NOW, GENERATOR_VERSION, GeneratorError, ShopData, ShopParams,
    fmt_ts, generate, parse_ts,
)
from .migrate_v3 import MigrationError, V3_SCHEMA_VERSION, apply_schema_v3, has_schema_v3
from .seed_csv import SeedError, load_catalog_csv, load_notes_csv
from .manifest import (
    MANIFEST_FORMAT, ManifestError, build_manifest, load_manifest, verify_manifest,
    write_manifest,
)
from .deploy import deploy_database
from .schema import (
    EPISODE_MUTABLE_TABLES, MANUAL_REVIEW_REASONS, PROVENANCES, QUESTION_CODES, REASON_CODES,
    REFUND_POLICY, REFUSAL_CODES, SCHEMA_VERSION,
)
from .trusted import TrustedWriteError, apply_schema_v2, has_schema_v2, record_trusted
from .store import (
    SUPPORTED_SCHEMA_VERSIONS, EpisodeStateManager, ShopSpec, Snapshot, SnapshotError,
    StoreError, SyntheticDataError, apply_schema_version, assert_synthetic, build_database,
    check_integrity, connect, diff_states, dump_state, load_snapshot, primary_key,
    restore_snapshot, state_hash, take_snapshot, verify_snapshot,
)

__all__ = [
    "TrustedWriteError", "apply_schema_v2", "has_schema_v2", "record_trusted",
    "MigrationError", "V3_SCHEMA_VERSION", "apply_schema_v3", "has_schema_v3",
    "SeedError", "load_catalog_csv", "load_notes_csv",
    "MANIFEST_FORMAT", "ManifestError", "build_manifest", "load_manifest",
    "verify_manifest", "write_manifest", "deploy_database",
    "SUPPORTED_SCHEMA_VERSIONS", "apply_schema_version",
    "FixtureError", "apply_fixture", "ARCHETYPES", "CATALOG", "DEFAULT_NOW",
    "GENERATOR_VERSION", "GeneratorError", "ShopData", "ShopParams", "fmt_ts", "generate",
    "parse_ts", "EPISODE_MUTABLE_TABLES", "MANUAL_REVIEW_REASONS", "PROVENANCES",
    "QUESTION_CODES", "REASON_CODES", "REFUND_POLICY", "REFUSAL_CODES", "SCHEMA_VERSION",
    "EpisodeStateManager", "ShopSpec", "Snapshot", "SnapshotError", "StoreError",
    "SyntheticDataError", "assert_synthetic", "build_database", "check_integrity", "connect",
    "diff_states", "dump_state", "load_snapshot", "primary_key", "restore_snapshot",
    "state_hash", "take_snapshot", "verify_snapshot",
]
