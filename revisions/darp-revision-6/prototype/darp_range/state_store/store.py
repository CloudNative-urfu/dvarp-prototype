"""Хранилище состояния: сборка БД, логический хеш, снимок, восстановление, дифф (NFR-2.1).

Снимок — полная копия файла SQLite через backup API плюс сопроводительный JSON с
логическим хешем. Восстановление удаляет целевой файл (и `-wal/-shm/-journal`), копирует
снимок и сверяет хеш: эпизод начинается только из проверенного состояния, иначе
`SnapshotError`. Логический хеш учитывает DDL всех объектов, `user_version` и все строки
в порядке по всем колонкам, поэтому ловит и изменение данных, и изменение схемы.

Дифф состояний (по первичному ключу) — сырьё для оценщика (FR-1.18): он сравнивает
состояние, а не текст агента.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..repro import hash_obj
from .generator import ShopData, ShopParams, generate, write_into
from .schema import FORBIDDEN_COLUMN_FRAGMENTS

SNAPSHOT_FORMAT = "darp-sqlite-snapshot-1"
_SIDE_SUFFIXES = ("-wal", "-shm", "-journal")


class StoreError(RuntimeError):
    pass


class SnapshotError(StoreError):
    """Снимок повреждён/подменён или восстановление дало не то состояние."""


class SyntheticDataError(StoreError):
    """Нарушена синтетичность данных (NFR-2.13)."""


# ---------------------------------------------------------------- соединения

def connect(path: str | Path, readonly: bool = False) -> sqlite3.Connection:
    """Соединение с включёнными внешними ключами. `readonly=True` — для гвардрейлов и
    оценщика: файл открывается в режиме `mode=ro` и любая запись падает (FR-1.8)."""
    path = Path(path)
    if readonly:
        if not path.exists():
            raise StoreError(f"нет БД: {path}")
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
    else:
        conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _remove_db_files(path: Path) -> None:
    for p in [path, *(path.with_name(path.name + s) for s in _SIDE_SUFFIXES)]:
        if p.exists():
            p.unlink()


# ---------------------------------------------------------------- сборка

@dataclass
class ShopSpec:
    """Результат сборки базовой БД: путь, seed, хеши и разметка аккаунтов (вне БД)."""
    path: str
    seed: int
    params: dict[str, Any]
    dataset_hash: str
    state_hash: str
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def build_database(path: str | Path, seed: int, params: ShopParams | None = None,
                   overwrite: bool = False) -> ShopSpec:
    path = Path(path)
    if path.exists():
        if not overwrite:
            raise StoreError(f"файл уже существует: {path}")
        _remove_db_files(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: ShopData = generate(seed, params)
    conn = connect(path)
    try:
        write_into(conn, data)
        check_integrity(conn)
        assert_synthetic(conn)
        h = state_hash(conn)
    finally:
        conn.close()
    return ShopSpec(path=str(path), seed=seed, params=data.params.as_dict(),
                    dataset_hash=data.dataset_hash(), state_hash=h, accounts=data.accounts)


# ---------------------------------------------------------------- состояние и хеш

def _with_conn(target: str | Path | sqlite3.Connection):
    if isinstance(target, sqlite3.Connection):
        return target, False
    return connect(target, readonly=True), True


def _encode(v: Any) -> Any:
    if isinstance(v, bytes):
        return {"$bytes": v.hex()}
    if isinstance(v, float):
        # REAL в схеме не используется; если появится — фиксированное представление
        return {"$real": repr(v)}
    return v


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name")]


def primary_key(conn: sqlite3.Connection, table: str) -> list[str]:
    cols = sorted((r[5], r[1]) for r in conn.execute(f"PRAGMA table_info('{table}')") if r[5])
    return [c for _, c in cols] or ["rowid"]


def dump_state(target: str | Path | sqlite3.Connection) -> dict[str, Any]:
    """Логическое состояние: схема (DDL всех объектов), user_version и строки таблиц."""
    conn, own = _with_conn(target)
    try:
        schema = [list(r) for r in conn.execute(
            "SELECT type, name, coalesce(sql, '') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name")]
        tables: dict[str, Any] = {}
        for t in _tables(conn):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")]
            order = ", ".join(str(i + 1) for i in range(len(cols)))
            rows = [[_encode(v) for v in r]
                    for r in conn.execute(f"SELECT * FROM '{t}' ORDER BY {order}")]
            tables[t] = {"columns": cols, "pk": primary_key(conn, t), "rows": rows}
        return {"user_version": conn.execute("PRAGMA user_version").fetchone()[0],
                "schema": schema, "tables": tables}
    finally:
        if own:
            conn.close()


def state_hash(target: str | Path | sqlite3.Connection) -> str:
    return hash_obj(dump_state(target))


def check_integrity(conn: sqlite3.Connection) -> None:
    res = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if res != "ok":
        raise StoreError(f"integrity_check: {res}")
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise StoreError(f"нарушены внешние ключи: {bad[:5]}")


# ---------------------------------------------------------------- синтетичность

_CARD_LIKE = re.compile(r"(?:\d[ -]?){13,19}")
TECHNICAL_META_KEYS = ("seed", "dataset_hash")


def assert_synthetic(target: str | Path | sqlite3.Connection) -> None:
    """NFR-2.13: метка synthetic, почта только на .example, нет колонок платёжных/контактных
    данных и строк, похожих на номер карты."""
    conn, own = _with_conn(target)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        if meta.get("synthetic") != "1":
            raise SyntheticDataError("в meta нет метки synthetic=1")
        for t in _tables(conn):
            for r in conn.execute(f"PRAGMA table_info('{t}')"):
                low = r[1].lower()
                if any(re.search(rf"(^|_){frag}($|_)", low) for frag in FORBIDDEN_COLUMN_FRAGMENTS):
                    raise SyntheticDataError(f"запрещённая колонка {t}.{r[1]}")
        for (email,) in conn.execute("SELECT email FROM customers"):
            if not email.lower().endswith(".example"):
                raise SyntheticDataError(f"почта вне домена .example: {email}")
        for t in _tables(conn):
            text_cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")
                         if (r[2] or "").upper() == "TEXT"]
            for c in text_cols:
                sql = f"SELECT \"{c}\" FROM '{t}' WHERE \"{c}\" IS NOT NULL"
                if t == "meta" and c == "value":
                    # Технические поля генератора (seed до 19 цифр, хеш) — не данные клиента:
                    # производный seed 3939378268348568937 случайно проходит проверку Луна.
                    sql += " AND key NOT IN (" + ",".join(f"'{k}'" for k in TECHNICAL_META_KEYS) + ")"
                for (v,) in conn.execute(sql):
                    # Luhn — только по самим цифровым фрагментам: склейка цифр всей строки
                    # (например, hex-хеша) давала ложные срабатывания.
                    if any(_luhn_ok(re.sub(r"\D", "", m.group(0)))
                           for m in _CARD_LIKE.finditer(str(v))):
                        raise SyntheticDataError(f"строка похожа на номер карты в {t}.{c}")
    finally:
        if own:
            conn.close()


def _luhn_ok(digits: str) -> bool:
    for n in range(19, 12, -1):
        for i in range(0, len(digits) - n + 1):
            chunk = digits[i:i + n]
            total = 0
            for j, ch in enumerate(reversed(chunk)):
                d = int(ch)
                if j % 2:
                    d *= 2
                    d -= 9 if d > 9 else 0
                total += d
            if total % 10 == 0:
                return True
    return False


# ---------------------------------------------------------------- снимки

@dataclass(frozen=True)
class Snapshot:
    path: str
    state_hash: str
    label: str
    seed: int | None

    @property
    def meta_path(self) -> Path:
        return Path(self.path + ".json")


def take_snapshot(db_path: str | Path, snap_path: str | Path, label: str = "baseline") -> Snapshot:
    """Полная копия БД через backup API + сопроводительный JSON с логическим хешем."""
    db_path, snap_path = Path(db_path), Path(snap_path)
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    _remove_db_files(snap_path)
    src = connect(db_path)
    try:
        check_integrity(src)
        h = state_hash(src)
        seed_row = src.execute("SELECT value FROM meta WHERE key='seed'").fetchone()
        dst = sqlite3.connect(snap_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if state_hash(snap_path) != h:
        raise SnapshotError("копия снимка не совпала с источником по хешу")
    snap = Snapshot(path=str(snap_path), state_hash=h, label=label,
                    seed=int(seed_row[0]) if seed_row else None)
    snap.meta_path.write_text(json.dumps(
        {"format": SNAPSHOT_FORMAT, "label": label, "seed": snap.seed, "state_hash": h},
        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return snap


def load_snapshot(snap_path: str | Path) -> Snapshot:
    snap_path = Path(snap_path)
    meta_path = Path(str(snap_path) + ".json")
    if not snap_path.exists() or not meta_path.exists():
        raise SnapshotError(f"нет файла снимка или его метаданных: {snap_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("format") != SNAPSHOT_FORMAT:
        raise SnapshotError(f"неизвестный формат снимка: {meta.get('format')!r}")
    return Snapshot(path=str(snap_path), state_hash=meta["state_hash"], label=meta["label"],
                    seed=meta.get("seed"))


def verify_snapshot(snap: Snapshot) -> None:
    try:
        actual = state_hash(snap.path)
    except sqlite3.DatabaseError as exc:
        raise SnapshotError(f"снимок не читается: {exc}") from exc
    if actual != snap.state_hash:
        raise SnapshotError(f"снимок {snap.path} изменён: хеш {actual[:12]} ≠ {snap.state_hash[:12]}")


def _copy_db(src_path: Path, dst_path: Path) -> None:
    """Копия БД через backup API (источник открыт только на чтение)."""
    src = sqlite3.connect(f"{src_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def restore_snapshot(snap: Snapshot, target: str | Path) -> str:
    """Полностью заменяет `target` содержимым снимка; возвращает проверенный хеш."""
    target = Path(target)
    verify_snapshot(snap)
    target.parent.mkdir(parents=True, exist_ok=True)
    _remove_db_files(target)
    _copy_db(Path(snap.path), target)
    h = state_hash(target)
    if h != snap.state_hash:
        raise SnapshotError(f"после восстановления хеш {h[:12]} ≠ {snap.state_hash[:12]}")
    return h


# ---------------------------------------------------------------- дифф

def diff_states(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Построчный дифф по первичному ключу: added/removed/changed для каждой таблицы,
    плюс флаг изменения схемы. Таблицы без изменений в результат не попадают."""
    out: dict[str, Any] = {}
    if before["schema"] != after["schema"] or before["user_version"] != after["user_version"]:
        out["__schema__"] = {"changed": True}
    for t in sorted(set(before["tables"]) | set(after["tables"])):
        b = before["tables"].get(t)
        a = after["tables"].get(t)
        ref = a or b
        cols = ref["columns"]
        pk = ref["pk"]
        if pk == ["rowid"]:
            key_idx = list(range(len(cols)))  # без PK: строка целиком как ключ
        else:
            key_idx = [cols.index(c) for c in pk]

        def index(tbl):
            if tbl is None:
                return {}
            return {json.dumps([r[i] for i in key_idx]): dict(zip(tbl["columns"], r))
                    for r in tbl["rows"]}
        bi, ai = index(b), index(a)
        added = [ai[k] for k in sorted(ai.keys() - bi.keys())]
        removed = [bi[k] for k in sorted(bi.keys() - ai.keys())]
        changed = [{"before": bi[k], "after": ai[k]} for k in sorted(ai.keys() & bi.keys())
                   if ai[k] != bi[k]]
        if added or removed or changed:
            out[t] = {"added": added, "removed": removed, "changed": changed}
    return out


# ---------------------------------------------------------------- эпизоды

@dataclass
class EpisodeRecord:
    episode_id: str
    path: str
    restored_hash: str
    final_hash: str | None = None


class EpisodeStateManager:
    """Жизненный цикл состояния эпизодов: одна базовая БД и снимок; перед каждым эпизодом —
    восстановление в свой файл с проверкой хеша (NFR-2.1). Парные прогоны (с атаками и
    без) получают одинаковые начальные условия по построению."""

    def __init__(self, workdir: str | Path, seed: int, params: ShopParams | None = None, *,
                 schema_version: int = 1):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        base = self.workdir / "base.sqlite"
        self.spec = build_database(base, seed, params, overwrite=True)
        self.schema_version = schema_version
        if schema_version == 2:
            # ревизия 6 (boundary-2): доверенные таблицы до снимка; БД v1 не меняются
            from .trusted import apply_schema_v2
            conn = connect(base)
            try:
                apply_schema_v2(conn)
                check_integrity(conn)
                self.spec.state_hash = state_hash(conn)
            finally:
                conn.close()
        elif schema_version != 1:
            raise StoreError(f"schema_version {schema_version!r} не из (1, 2)")
        self.snapshot = take_snapshot(base, self.workdir / "baseline.snapshot.sqlite")
        os.chmod(self.snapshot.path, 0o444)  # защита снимка от случайной записи
        self._baseline_state = dump_state(self.snapshot.path)
        self.episodes: dict[str, EpisodeRecord] = {}

    @property
    def baseline_hash(self) -> str:
        return self.snapshot.state_hash

    def prepare(self, episode_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", episode_id):
            raise StoreError(f"недопустимый id эпизода: {episode_id!r}")
        path = self.workdir / "episodes" / f"{episode_id}.sqlite"
        h = restore_snapshot(self.snapshot, path)
        self.episodes[episode_id] = EpisodeRecord(episode_id, str(path), h)
        return path

    def finish(self, episode_id: str) -> dict[str, Any]:
        """Итог эпизода: финальный хеш и дифф относительно базового снимка."""
        rec = self.episodes.get(episode_id)
        if rec is None:
            raise StoreError(f"эпизод {episode_id} не подготовлен")
        final = dump_state(rec.path)
        rec.final_hash = hash_obj(final)
        return {"episode_id": episode_id, "restored_hash": rec.restored_hash,
                "final_hash": rec.final_hash,
                "diff": diff_states(self._baseline_state, final)}
