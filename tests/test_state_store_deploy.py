"""Проверки деплоя БД из CSV-сида, миграции v3 и манифеста привязки трафика.

Покрывает free-пункты дизайн-дока `docs/refund-db-design.md` (issue #37 → #38):
индексы и колонки аудита (№6–8, 10, 11), CSV-сид и деплой (№14), манифест
привязки легальных эпизодов к реальным строкам базы (№13, #36/#19).
Только офлайн.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from darp_range.state_store import (
    CATALOG, EpisodeStateManager, ManifestError, SeedError, apply_schema_v3, build_database,
    connect, deploy_database, dump_state, has_schema_v3, load_catalog_csv, load_manifest,
    load_notes_csv, state_hash, verify_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "darp_range" / "state_store" / "seed"


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        for p in self.tmp.rglob("*"):
            if p.is_file():
                os.chmod(p, 0o644)
        self._td.cleanup()


class MigrationV3Tests(_Tmp):
    def test_columns_and_indexes_applied_idempotent(self):
        spec = build_database(self.tmp / "db.sqlite", 7)
        conn = connect(spec.path)
        self.assertFalse(has_schema_v3(conn))
        apply_schema_v3(conn)
        cols = {t: {r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")}
                for t in ("manual_reviews", "info_requests", "ops_ledger")}
        self.assertIn("decided_at", cols["manual_reviews"])
        self.assertIn("decision", cols["manual_reviews"])
        self.assertIn("decided_by", cols["manual_reviews"])
        self.assertIn("question_text", cols["info_requests"])
        self.assertIn("detail_json", cols["ops_ledger"])
        idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for name in ("idx_refunds_ticket", "idx_refusals_ticket", "idx_info_ticket",
                     "idx_tickets_customer_status", "idx_reviews_queue"):
            self.assertIn(name, idx)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertEqual(conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "3")
        # идемпотентность: повтор не меняет состояние
        h = state_hash(conn)
        apply_schema_v3(conn)
        self.assertEqual(state_hash(conn), h)
        conn.close()

    def test_migration_only_on_v1_v2(self):
        spec = build_database(self.tmp / "db.sqlite", 7)
        conn = connect(spec.path)
        conn.execute("PRAGMA user_version = 9")
        with self.assertRaises(RuntimeError):
            apply_schema_v3(conn)
        conn.close()

    def test_episode_manager_schema_versions(self):
        v1 = EpisodeStateManager(self.tmp / "v1", seed=7)
        v2 = EpisodeStateManager(self.tmp / "v2", seed=7, schema_version=2)
        v3 = EpisodeStateManager(self.tmp / "v3", seed=7, schema_version=3)
        # каждая версия схемы — свой логический хеш (DDL входит в dump_state)
        self.assertNotEqual(v1.baseline_hash, v2.baseline_hash)
        self.assertNotEqual(v2.baseline_hash, v3.baseline_hash)
        # воспроизводимость и восстановление снимка на v3
        p1 = v3.prepare("ep-1")
        self.assertEqual(state_hash(p1), v3.baseline_hash)
        self.assertEqual(v3.finish("ep-1")["diff"], {})
        v3b = EpisodeStateManager(self.tmp / "v3b", seed=7, schema_version=3)
        self.assertEqual(v3b.baseline_hash, v3.baseline_hash)


class SeedCsvTests(_Tmp):
    def test_builtin_catalog_from_csv_matches_builtin_hash(self):
        csv_rows = load_catalog_csv(SEED_DIR / "catalog.csv")
        self.assertEqual(csv_rows, list(CATALOG))
        a = build_database(self.tmp / "a.sqlite", 7)
        b = build_database(self.tmp / "b.sqlite", 7, catalog=csv_rows,
                           benign_notes=load_notes_csv(SEED_DIR / "benign_notes.csv"))
        self.assertEqual(a.state_hash, b.state_hash)
        self.assertEqual(a.dataset_hash, b.dataset_hash)

    def test_changed_catalog_changes_dataset(self):
        csv_rows = [list(r) for r in load_catalog_csv(SEED_DIR / "catalog.csv")]
        csv_rows[0][3] = 9001                        # цена одной позиции
        a = build_database(self.tmp / "a.sqlite", 7)
        b = build_database(self.tmp / "b.sqlite", 7, catalog=[tuple(r) for r in csv_rows])
        self.assertNotEqual(a.state_hash, b.state_hash)

    def test_bad_csv_rejected(self):
        bad = self.tmp / "bad.csv"
        bad.write_text("sku,title,category,price_cents\nSKU-1,Boots,shoes,1000\n", encoding="utf-8")
        with self.assertRaises(SeedError):
            load_catalog_csv(bad)                    # нет колонки final_sale
        bad.write_text("sku,title,category,price_cents,final_sale\n"
                       "SKU-1,Boots,shoes,1000,1\nSKU-1,Mug,home,100,0\n", encoding="utf-8")
        with self.assertRaises(SeedError):
            load_catalog_csv(bad)                    # повторяющийся sku
        bad.write_text("sku,title,category,price_cents,final_sale\n"
                       "SKU-1,Boots,shoes,-5,0\n", encoding="utf-8")
        with self.assertRaises(SeedError):
            load_catalog_csv(bad)                    # неположительная цена
        empty = self.tmp / "empty.csv"
        empty.write_text("", encoding="utf-8")
        with self.assertRaises(SeedError):
            load_notes_csv(empty)                    # файл пуст
        notes = self.tmp / "notes.csv"
        notes.write_text("a note\n\nanother\n", encoding="utf-8")
        self.assertEqual(load_notes_csv(notes), ["a note", None, "another"])


class ManifestTests(_Tmp):
    def _deploy(self):
        return deploy_database(
            self.tmp / "db.sqlite", 7, schema_version=3, overwrite=True,
            snapshot_path=self.tmp / "snap.sqlite", manifest_path=self.tmp / "m.json")

    def test_manifest_roundtrip_and_real_rows(self):
        r = self._deploy()
        manifest = load_manifest(self.tmp / "m.json")
        self.assertEqual(manifest["state_hash"], r["state_hash"])
        self.assertTrue(manifest["orders"])
        for o in manifest["orders"].values():
            self.assertTrue(o["items"])
            self.assertIn(o["archetype"],
                          ("recent", "old", "final_sale", "high_value", "in_transit",
                           "recently_refunded"))
        verify_manifest(manifest, self.tmp / "db.sqlite")
        # детерминизм: тот же seed — идентичный манифест
        self._deploy()
        self.assertEqual((self.tmp / "m.json").read_bytes(), (self.tmp / "m.json").read_bytes())

    def test_manifest_detects_db_tamper(self):
        self._deploy()
        manifest = load_manifest(self.tmp / "m.json")
        conn = connect(self.tmp / "db.sqlite")
        conn.execute("UPDATE order_items SET price_cents = price_cents + 1 WHERE id = 1")
        conn.commit()
        with self.assertRaises(ManifestError):
            verify_manifest(manifest, self.tmp / "db.sqlite")
        conn.close()

    def test_manifest_detects_missing_row(self):
        r = self._deploy()
        manifest = load_manifest(self.tmp / "m.json")
        conn = connect(self.tmp / "db.sqlite")
        # позиция без исторической заявки: её удаление не нарушает внешние ключи
        (item_id,) = conn.execute(
            "SELECT i.id FROM order_items i LEFT JOIN refund_requests r "
            "ON r.order_item_id = i.id WHERE r.id IS NULL LIMIT 1").fetchone()
        conn.execute("DELETE FROM order_items WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()
        # урезанная БД не проходит проверку манифеста: расходятся хеш и состав позиций
        tampered = {**manifest, "state_hash": r["state_hash"]}
        with self.assertRaises(ManifestError):
            verify_manifest(tampered, self.tmp / "db.sqlite")


class DeployTests(_Tmp):
    def _cli(self, *args):
        return subprocess.run([sys.executable, "-m", "darp_range.state_store", *args], cwd=ROOT,
                              capture_output=True, text=True)

    def test_deploy_without_csv_equals_builtin(self):
        a = build_database(self.tmp / "a.sqlite", 7)
        r = self._cli("deploy", "--seed", "7", "--out", str(self.tmp / "b.sqlite"),
                      "--schema", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["state_hash"], a.state_hash)

    def test_deploy_csv_v3_snapshot_manifest(self):
        r = self._cli("deploy", "--seed", "7", "--out", str(self.tmp / "db.sqlite"),
                      "--catalog", str(SEED_DIR / "catalog.csv"),
                      "--notes", str(SEED_DIR / "benign_notes.csv"),
                      "--schema", "3", "--snapshot", str(self.tmp / "snap.sqlite"),
                      "--manifest", str(self.tmp / "m.json"), "--overwrite")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["schema_version"], 3)
        self.assertEqual(out["state_hash"], out["manifest_state_hash"])
        self.assertEqual(out["state_hash"], out["snapshot"]["state_hash"])
        v = self._cli("check-manifest", str(self.tmp / "db.sqlite"), str(self.tmp / "m.json"))
        self.assertEqual(v.returncode, 0, v.stderr)
        d = dump_state(self.tmp / "db.sqlite")
        self.assertIn("detail_json", d["tables"]["ops_ledger"]["columns"])

    def test_deploy_rejects_overwrite_and_bad_schema(self):
        r = self._cli("deploy", "--seed", "7", "--out", str(self.tmp / "db.sqlite"),
                      "--schema", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._cli("deploy", "--seed", "7", "--out",
                                   str(self.tmp / "db.sqlite"), "--schema", "1").returncode, 2)
        self.assertEqual(self._cli("deploy", "--seed", "7", "--out",
                                   str(self.tmp / "x.sqlite"), "--schema", "4").returncode, 2)


if __name__ == "__main__":
    unittest.main()
