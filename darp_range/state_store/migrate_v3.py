"""Миграция схемы v1/v2 → v3: продуктовые поля аудита и индексы (issue #37/#38).

Дизайн-док: `docs/refund-db-design.md` §2.3/§2.5/§2.6/§2.7/§2.8/§3/§8 (пункты 6–11).
Добавляются только nullable-колонки, индексы и расширение закрытого словаря
`refund_requests.status`, поэтому замороженный код (`executor`, `tools`),
который вставляет явные списки колонок и значения словаря v1, продолжает
работать без перезаморозки; новые поля остаются NULL до появления
не-замороженного механизма записи (список перезаморозки — §8.1 дизайн-дока).

Миграция меняет логический хеш состояния (DDL входит в `dump_state`), поэтому
применяется только к БД, развернутым с `schema_version=3`
(`state_store.deploy`, `EpisodeStateManager(schema_version=3)`); базовые БД
v1/v2 и их снимки не трогаются (контракт #19: правка схемы — перегенерация
снимков, не перезаморозка защиты).

Примечание: это v3 эпизодных/базовых БД (`state_store`). Отдельная v3 БД
контура возвратов — `darp_range/shop_svc/migrate.py` (ревизия 11, только для
slice-БД); эти миграции не пересекаются и не вызывают друг друга.

Идемпотентно: повторный вызов не меняет состояние. Только стандартная
библиотека.
"""

from __future__ import annotations

import sqlite3

from .schema import REASON_CODES, _in
from .store import connect

V3_SCHEMA_VERSION = 3

# Расширенный словарь статусов заявки: v1 ('created', 'paid_synthetic') +
# исходы жизненного цикла (design §2.5). Значения (кроме v1) пишет
# не-замороженный механизм записи после перезаморозки исполнителя; словарь
# закрывает их на уровне схемы уже сейчас.
REFUND_STATUS_V3 = ("created", "paid_synthetic", "approved", "rejected", "cancelled")

# Исходы решения оператора по ручной проверке (design §2.6). Пишет не-замороженный
# механизм записи; валидация значений — на стороне писателя.
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
    # момент закрытия тикета (design §2.3; до перезаморозки восстановим из
    # ops_ledger-ключа final:<ticket> на стороне читателя)
    ("tickets", "closed_at", "TEXT"),
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

# Перестройка refund_requests: единственный способ расширить CHECK словаря статусов
# (ALTER не меняет ограничения). Колонки, их порядок и ограничения — как в v1
# (`darp_range/state_store/schema.py:123`), кроме словаря статусов.
_REFUND_REQUESTS_V3 = f"""
CREATE TABLE refund_requests_v3 (
    id            INTEGER PRIMARY KEY,
    ticket_id     TEXT REFERENCES tickets(id),
    order_item_id INTEGER NOT NULL UNIQUE REFERENCES order_items(id),
    customer_id   TEXT NOT NULL REFERENCES customers(id),
    amount_cents  INTEGER NOT NULL CHECK (amount_cents > 0),
    reason_code   TEXT NOT NULL CHECK (reason_code IN {_in(REASON_CODES)}),
    status        TEXT NOT NULL CHECK (status IN {_in(REFUND_STATUS_V3)}),
    created_by    TEXT NOT NULL CHECK (created_by IN ('executor', 'history')),
    created_at    TEXT NOT NULL
);
INSERT INTO refund_requests_v3 SELECT id, ticket_id, order_item_id, customer_id, amount_cents,
    reason_code, status, created_by, created_at FROM refund_requests;
DROP TABLE refund_requests;
ALTER TABLE refund_requests_v3 RENAME TO refund_requests;
CREATE INDEX idx_refunds_customer ON refund_requests(customer_id, created_at);
"""


class MigrationError(RuntimeError):
    """Миграция v3 неприменима к этой БД."""


def has_schema_v3(conn: sqlite3.Connection) -> bool:
    return conn.execute("PRAGMA user_version").fetchone()[0] == V3_SCHEMA_VERSION


def has_v3_columns(conn: sqlite3.Connection) -> bool:
    """Все колонки аудита v3 на месте. Писатели v3-полей (исполнитель, #57) смотрят на колонки, а
    не на `user_version`: тройку ставит и слайсовая миграция контура (`shop_svc/migrate.py`)."""
    return not _missing_columns(conn)


def _missing_columns(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    missing: list[tuple[str, str, str]] = []
    for table, col, typ in V3_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")}
        if col not in cols:
            missing.append((table, col, typ))
    return missing


def _check_applicable(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in (1, 2, V3_SCHEMA_VERSION):
        raise MigrationError(
            f"миграция v3 применима к БД v1/v2, получено user_version={version}")
    base = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'defect_evidence' "
                        "AND name NOT LIKE 'refund_approvals'").fetchone()[0]
    if base == 0:
        raise MigrationError("миграция v3 применяется к непустой БД магазина")


def apply_schema_v3(conn: sqlite3.Connection) -> None:
    """Добавляет колонки аудита, расширяет словарь статусов заявок, создает индексы,
    ставит `user_version=3` и метку в meta."""
    if has_schema_v3(conn):
        return
    _check_applicable(conn)
    ddl = "".join(f"ALTER TABLE {t} ADD COLUMN {c} {typ};\n" for t, c, typ in _missing_columns(conn))
    # внешние ключи на время перестройки таблицы: у refund_requests нет ссылающихся
    # таблиц, но OFF повторяет образец `shop_svc.migrate.apply_schema_v3`
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript("BEGIN;\n" + ddl + _REFUND_REQUESTS_V3 + "\nCOMMIT;")
    except sqlite3.DatabaseError:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise MigrationError(f"foreign key violations after v3: {bad[:3]}")
    for name, table, cols in V3_INDEXES:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({cols})")
    conn.execute(f"PRAGMA user_version = {V3_SCHEMA_VERSION}")
    with conn:
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
