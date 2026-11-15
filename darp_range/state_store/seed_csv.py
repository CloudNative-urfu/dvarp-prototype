"""CSV-сид каталога и безобидных заметок заказа (issue #38; дизайн-док §7).

Источник данных для деплоя (`state_store.deploy`): каталог товаров и заметки
заказов читаются из CSV, далее работает обычный детерминированный генератор
(FR-1.10) — CSV подменяет только содержимое, а не механику. Состав сида входит
в `dataset_hash` через сгенерированные строки, поэтому одинаковые seed + CSV
дают побайтно одинаковое состояние.

Форматы и валидация:
- `catalog.csv` — колонки `sku,title,category,price_cents,final_sale`; деньги
  в целых центах (NFR-2.13), `final_sale` — 0/1, тексты английские
  (NFR-2.7), sku уникальны;
- `benign_notes.csv` — без заголовка, одна колонка `note`, одна заметка на
  строку; пустая строка файла означает «заметки нет» (None) — так CSV-сид
  может повторить разреженность встроенного набора `_BENIGN_NOTES`.

Нарушения — `SeedError`. Только стандартная библиотека.
"""

from __future__ import annotations

import csv
from pathlib import Path

CATALOG_FIELDS = ("sku", "title", "category", "price_cents", "final_sale")
NOTES_FIELDS = ("note",)


class SeedError(ValueError):
    """CSV-сид не соответствует формату или содержит некорректные данные."""


def _check_text(value: str, where: str) -> str:
    text = value.strip()
    if not text:
        raise SeedError(f"{where}: пустое значение")
    if not text.isascii():
        raise SeedError(f"{where}: только английский текст (NFR-2.7)")
    return text


def load_catalog_csv(path: str | Path) -> list[tuple[str, str, str, int, int]]:
    """Читает каталог: список (sku, title, category, price_cents, final_sale)."""
    rows: list[tuple[str, str, str, int, int]] = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != CATALOG_FIELDS:
            raise SeedError(f"{path}: колонки должны быть {CATALOG_FIELDS}")
        for i, row in enumerate(reader, start=2):
            where = f"{path}: строка {i}"
            sku = _check_text(row["sku"], where)
            title = _check_text(row["title"], where)
            category = _check_text(row["category"], where)
            try:
                price = int(row["price_cents"])
            except (TypeError, ValueError):
                raise SeedError(f"{where}: price_cents — целое") from None
            if price <= 0:
                raise SeedError(f"{where}: price_cents > 0")
            fs = (row["final_sale"] or "").strip()
            if fs not in ("0", "1"):
                raise SeedError(f"{where}: final_sale — 0 или 1")
            rows.append((sku, title, category, price, int(fs)))
    if not rows:
        raise SeedError(f"{path}: каталог пуст")
    skus = [sku for sku, *_ in rows]
    if len(set(skus)) != len(skus):
        dupes = sorted({s for s in skus if skus.count(s) > 1})
        raise SeedError(f"{path}: повторяющиеся sku: {dupes}")
    return rows


def load_notes_csv(path: str | Path) -> list[str | None]:
    """Читает заметки: список строк; пустая строка файла — None (заметки нет)."""
    notes: list[str | None] = []
    with open(path, encoding="utf-8", newline="") as f:
        for i, raw in enumerate(csv.reader(f), start=1):
            if not raw:                       # пустая строка файла = заметки нет
                notes.append(None)
                continue
            if len(raw) != 1:
                raise SeedError(f"{path}: строка {i}: ровно одна колонка note")
            text = raw[0].strip()
            if text and not text.isascii():
                raise SeedError(f"{path}: строка {i}: только английский текст (NFR-2.7)")
            notes.append(text or None)
    if not notes:
        raise SeedError(f"{path}: файл пуст")
    return notes
