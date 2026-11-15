"""Манифест привязки легального трафика к реальным строкам БД (issue #36/#38).

Решение тимлида в #19: приглашение эпизода несёт id+хеш снимка БД, а легальные
эпизоды привязываются к реальным строкам базы заказов (консистентность с
R1–R7 и ground truth, не фиктивные id). Манифест — детерминированный JSON
рядом с БД/снимком: хеши сида, разбивка аккаунтов по архетипам (разметка ролей
живет вне БД, FR-1.3) и фактические id заказов/позиций, прочитанные из БД.

Манифест не пишется в БД и не входит в её логический хеш: он выводится из тех
же строк, поэтому расхождение с БД невозможно по построению, а
`verify_manifest` дополнительно сверяет логический хеш и существование/поля
каждой ссылки. Потребитель — генератор легального трафика (traffic-svc, #36)
и оркестратор. Только стандартная библиотека.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .store import ShopSpec, connect, state_hash

MANIFEST_FORMAT = "darp-refund-db-manifest-1"


class ManifestError(ValueError):
    """Манифест не соответствует формату или расходится с БД."""


def build_manifest(db_path: str | Path, spec: ShopSpec) -> dict[str, Any]:
    """Собирает манифест из БД и ShopSpec (аккаунты/архетипы — вне БД)."""
    conn = connect(db_path, readonly=True)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        items: dict[int, list[dict[str, Any]]] = {}
        for r in conn.execute(
                "SELECT id, order_id, sku, price_cents, qty, final_sale "
                "FROM order_items ORDER BY id"):
            items.setdefault(r[1], []).append({
                "order_item_id": r[0], "sku": r[2], "price_cents": r[3],
                "qty": r[4], "final_sale": bool(r[5])})
        archetype: dict[str, tuple[str, str]] = {}     # order_id -> (customer_id, archetype)
        for cid, acc in sorted(spec.accounts.items()):
            for arch, oid in sorted(acc["orders"].items()):
                archetype[oid] = (cid, arch)
        orders: dict[str, Any] = {}
        for r in conn.execute(
                "SELECT id, customer_id, status, total_cents, delivered_at FROM orders"):
            oid = r[0]
            if oid not in archetype:
                raise ManifestError(f"в БД есть заказ {oid} вне архетипов аккаунтов")
            cid, arch = archetype.pop(oid)
            if r[1] != cid:
                raise ManifestError(f"заказ {oid}: владелец в БД {r[1]} ≠ аккаунту {cid}")
            orders[oid] = {"customer_id": cid, "archetype": arch, "status": r[2],
                           "total_cents": r[3], "delivered_at": r[4],
                           "items": items.get(oid, [])}
        if archetype:
            raise ManifestError(f"аккаунты ссылаются на отсутствующие заказы: {sorted(archetype)}")
    finally:
        conn.close()
    return {
        "format": MANIFEST_FORMAT,
        "seed": spec.seed,
        "params": spec.params,
        "dataset_hash": spec.dataset_hash,
        "state_hash": spec.state_hash,
        "now": meta.get("now"),
        "accounts": {cid: {"role": acc["role"], "orders": acc["orders"]}
                     for cid, acc in sorted(spec.accounts.items())},
        "orders": orders,
    }


def write_manifest(manifest: dict[str, Any], path: str | Path) -> Path:
    """Пишет детерминированный JSON (ключи отсортированы, фиксированный отступ)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("format") != MANIFEST_FORMAT:
        raise ManifestError(f"неизвестный формат манифеста: {manifest.get('format')!r}")
    return manifest


def verify_manifest(manifest: dict[str, Any], db_path: str | Path) -> None:
    """Манифест соответствует БД: хеш состояния и существование/поля всех ссылок."""
    if state_hash(db_path) != manifest.get("state_hash"):
        raise ManifestError("логический хеш БД не совпал с манифестом")
    conn = connect(db_path, readonly=True)
    try:
        customers = {r[0] for r in conn.execute("SELECT id FROM customers")}
        for cid, acc in manifest["accounts"].items():
            if cid not in customers:
                raise ManifestError(f"клиента {cid} нет в БД")
        db_orders = conn.execute("SELECT count(*) FROM orders").fetchone()[0]
        if db_orders != len(manifest["orders"]):
            raise ManifestError(f"манифест покрывает {len(manifest['orders'])} заказов, "
                                f"в БД {db_orders}")
        for oid, o in manifest["orders"].items():
            row = conn.execute(
                "SELECT customer_id, status, total_cents, coalesce(delivered_at, '') "
                "FROM orders WHERE id = ?", (oid,)).fetchone()
            if row is None:
                raise ManifestError(f"заказа {oid} нет в БД")
            if (row[0], row[1], row[2], row[3]) != (o["customer_id"], o["status"],
                                                    o["total_cents"],
                                                    o["delivered_at"] or ""):
                raise ManifestError(f"заказ {oid}: поля расходятся с БД")
            db_items = {r[0]: r for r in conn.execute(
                "SELECT id, sku, price_cents, qty, final_sale FROM order_items "
                "WHERE order_id = ?", (oid,))}
            for it in o["items"]:
                r = db_items.get(it["order_item_id"])
                if r is None:
                    raise ManifestError(f"позиции {it['order_item_id']} нет в заказе {oid}")
                if (r[1], r[2], r[3], bool(r[4])) != (it["sku"], it["price_cents"],
                                                      it["qty"], it["final_sale"]):
                    raise ManifestError(f"позиция {it['order_item_id']}: поля расходятся с БД")
    finally:
        conn.close()
