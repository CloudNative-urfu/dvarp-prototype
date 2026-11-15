"""Миграция схемы v1/v2 → v3: продуктовые поля аудита и индексы (issue #37/#38).

Дизайн-док: `docs/refund-db-design.md` §2.6/§2.7/§2.8/§3/§8 (пункты 6–8, 10, 11).
Добавляются только nullable-колонки и индексы, поэтому замороженный код
(`executor`, `tools`), который вставляет явные списки колонок, продолжает
работать без перезаморозки; новые поля остаются NULL до появления
не-замороженного механизма записи (список перезаморозки — §8.1 дизайн-дока).

Миграция меняет логический хеш состояния (DDL входит в `dump_state`), поэтому
применяется только к БД, развернутым с `schema_version=3`
(`state_store.deploy`, `EpisodeStateManager(schema_version=3)`); базовые БД
v1/v2 и их снимки не трогаются (контракт #19: правка схемы — перегенерация
снимков, не перезаморозка защиты).

Идемпотентно: повторный вызов не меняет состояние. Только стандартная
библиотека.
"""

from __future__ import annotations

import sqlite3

from .store import connect

V3_SCHEMA_VERSION = 3

# Исходы решения оператора по ручной проверке (design §2.6). Значения пишет
# не-замороженный механизм записи после перезаморозки исполнителя; колонка
# nullable без CHECK — валидацию выполняет писатель.
REVIEW_DECISIONS = ("approved", "rejected")

# Nullable-колонки аудита: (таблица, колонка, тип).
V3_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # исходы очереди операторов: E(g), Q(g), P(W_q > Q_SLA) — канон dvarp#18
    ("manual_reviews", "decided_at", "TEXT"),
    ("manual_reviews", "decision", "TEXT"),
    ("manual_reviews", "decided_by", "TEXT"),
    # текст заданного вопроса (аудит уточнений R7; канонический текст по коду — tools/specs.py)
    ("info_requests", "question_text", "TEXT"),
    # payload разовой операции для аудита уровня БД (без EventLog)
    ("ops_ledger", "detail_json", "TEXT"),
)

# Индексы: (имя, таблица, колонки). Запросы, ради которых они добавлены:
# refund_requests(ticket_id) — идемпотентный повтор исполнителя (executor.py `_replay`)
# и сводка оценщика; refusals/info_requests(ticket_id) — дифф/дедупликация по обращению;
# tickets(customer_id, status) — список открытых обращений клиента;
# manual_reviews(enqueued_at) — очередь операторов (после появления decided_at —
# вычисление задержки обслуживания Q(g) на стороне читателя).
V3_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("idx_refunds_ticket", "refund_requests", "ticket_id"),
    ("idx_refusals_ticket", "refusals", "ticket_id"),
    ("idx_info_ticket", "info_requests", "ticket_id"),
    ("idx_tickets_customer_status", "tickets", "customer_id, status"),
    ("idx_reviews_queue", "manual_reviews", "enqueued_at"),
)


class MigrationError(RuntimeError):
    """Миграция v3 неприменима к этой БД."""


def has_schema_v3(conn: sqlite3.Connection) -> bool:
    return conn.execute("PRAGMA user_version").fetchone()[0] == V3_SCHEMA_VERSION


def _missing_columns(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    missing: list[tuple[str, str, str]] = []
    for table, col, typ in V3_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")}
        if col not in cols:
            missing.append((table, col, typ))
    return missing


def apply_schema_v3(conn: sqlite3.Connection) -> None:
    """Добавляет колонки аудита и индексы, ставит `user_version=3` и метку в meta."""
    if has_schema_v3(conn):
        return
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in (1, 2, V3_SCHEMA_VERSION):
        raise MigrationError(f"миграция v3 применима к БД v1/v2, получено user_version={version}")
    base = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'defect_evidence' "
                        "AND name NOT LIKE 'refund_approvals'").fetchone()[0]
    if base == 0:
        raise MigrationError("миграция v3 применяется к непустой БД магазина")
    with conn:
        for table, col, typ in _missing_columns(conn):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        for name, table, cols in V3_INDEXES:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({cols})")
        conn.execute(f"PRAGMA user_version = {V3_SCHEMA_VERSION}")
        conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                     (str(V3_SCHEMA_VERSION),))


def upgrade(path: str, schema_version: int = V3_SCHEMA_VERSION) -> str:
    """Миграция БД по пути `path` до `schema_version` (сейчас только 3); возвращает хеш."""
    from .store import check_integrity, state_hash
    if schema_version != V3_SCHEMA_VERSION:
        raise MigrationError(f"поддерживается только схема v{V3_SCHEMA_VERSION}")
    conn = connect(path)
    try:
        apply_schema_v3(conn)
        check_integrity(conn)
        return state_hash(conn)
    finally:
        conn.close()
