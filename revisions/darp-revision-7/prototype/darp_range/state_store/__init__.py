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
from .schema import (
    EPISODE_MUTABLE_TABLES, MANUAL_REVIEW_REASONS, PROVENANCES, QUESTION_CODES, REASON_CODES,
    REFUND_POLICY, REFUSAL_CODES, SCHEMA_VERSION,
)
from .trusted import TrustedWriteError, apply_schema_v2, has_schema_v2, record_trusted
from .store import (
    EpisodeStateManager, ShopSpec, Snapshot, SnapshotError, StoreError, SyntheticDataError,
    assert_synthetic, build_database, check_integrity, connect, diff_states, dump_state,
    load_snapshot, primary_key, restore_snapshot, state_hash, take_snapshot, verify_snapshot,
)

__all__ = [
    "TrustedWriteError", "apply_schema_v2", "has_schema_v2", "record_trusted",
    "FixtureError", "apply_fixture", "ARCHETYPES", "CATALOG", "DEFAULT_NOW",
    "GENERATOR_VERSION", "GeneratorError", "ShopData", "ShopParams", "fmt_ts", "generate",
    "parse_ts", "EPISODE_MUTABLE_TABLES", "MANUAL_REVIEW_REASONS", "PROVENANCES",
    "QUESTION_CODES", "REASON_CODES", "REFUND_POLICY", "REFUSAL_CODES", "SCHEMA_VERSION",
    "EpisodeStateManager", "ShopSpec", "Snapshot", "SnapshotError", "StoreError",
    "SyntheticDataError", "assert_synthetic", "build_database", "check_integrity", "connect",
    "diff_states", "dump_state", "load_snapshot", "primary_key", "restore_snapshot",
    "state_hash", "take_snapshot", "verify_snapshot",
]
