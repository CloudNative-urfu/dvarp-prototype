"""Развертывание готовой БД сервиса возврата из сида (issue #38; дизайн-док §7).

Один вход для #36/#19: сборка детерминированной БД (опционально из CSV-сида),
миграции схемы до целевой версии, снимок с хешем и манифест привязки легального
трафика к реальным строкам базы. Все выходы детерминированы: одинаковые seed,
параметры и CSV дают побайтно одинаковое состояние и идентичный манифест.

CLI: `python3 -m darp_range.state_store deploy --help`. Только stdlib.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .generator import ShopParams
from .manifest import build_manifest, verify_manifest, write_manifest
from .store import (
    ShopSpec, Snapshot, StoreError, apply_schema_version, assert_synthetic, build_database,
    check_integrity, connect, state_hash, take_snapshot,
)

SUPPORTED_DEPLOY_SCHEMA_VERSIONS = (1, 2, 3)


def deploy_database(out_path: str | Path, seed: int, *,
                    params: ShopParams | None = None,
                    catalog=None, benign_notes=None,
                    schema_version: int = 3,
                    snapshot_path: str | Path | None = None,
                    manifest_path: str | Path | None = None,
                    overwrite: bool = False) -> dict[str, Any]:
    """Собирает БД и (опционально) снимок + манифест; возвращает сводку деплоя."""
    if schema_version not in SUPPORTED_DEPLOY_SCHEMA_VERSIONS:
        raise StoreError(f"schema_version {schema_version!r} не из "
                         f"{SUPPORTED_DEPLOY_SCHEMA_VERSIONS}")
    spec: ShopSpec = build_database(out_path, seed, params, overwrite=overwrite,
                                    catalog=catalog, benign_notes=benign_notes)
    if schema_version != 1:
        conn = connect(out_path)
        try:
            apply_schema_version(conn, schema_version)
            check_integrity(conn)
            spec.state_hash = state_hash(conn)
        finally:
            conn.close()
    # обязательные проверки выходного артефакта (issue #38, п. 2): синтетичность
    # и запрещённые колонки — после миграций, на финальной схеме
    assert_synthetic(out_path)
    result: dict[str, Any] = {"out": str(out_path), "seed": seed,
                              "schema_version": schema_version,
                              "dataset_hash": spec.dataset_hash,
                              "state_hash": spec.state_hash}
    snap: Snapshot | None = None
    if snapshot_path is not None:
        snap = take_snapshot(out_path, snapshot_path, label="deploy")
        result["snapshot"] = {"path": snap.path, "state_hash": snap.state_hash,
                              "label": snap.label, "seed": snap.seed}
    if manifest_path is not None:
        manifest = build_manifest(out_path, spec)
        verify_manifest(manifest, out_path)     # самопроверка: манифест ↔ собранная БД
        result["manifest"] = write_manifest(manifest, manifest_path).as_posix()
        result["manifest_state_hash"] = manifest["state_hash"]
    return result
