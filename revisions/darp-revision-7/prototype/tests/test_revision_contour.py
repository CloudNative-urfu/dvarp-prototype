"""Ревизия 1, критик №6: флаг локального контура по фактическому исполнителю модели.

Прежняя реализация считала живую модель «в контуре», если адрес endpoint — loopback, даже
когда сервер сообщал внешнего upstream-провайдера. На прежнем коде `classify_model_contour`
нет (ImportError — регрессия фиксируется в artifacts/checks/revision/regressions/).
"""

from __future__ import annotations

import unittest

from darp_range.report_cli.contour import classify_model_contour


class ModelContour(unittest.TestCase):
    def test_upstream_provider_means_external(self):
        r = classify_model_contour({"Darkbloom": 3}, (), ["qwen/qwen3.5-9b"])
        self.assertEqual(r["model_contour"], "external")
        self.assertFalse(r["local_execution_confirmed"])
        self.assertEqual(r["served_model_ids"], ["qwen/qwen3.5-9b"])

    def test_router_in_error_text_means_external(self):
        r = classify_model_contour({}, ['{"error": "HTTP 502: OpenRouter connection failed"}'])
        self.assertEqual(r["model_contour"], "external")

    def test_loopback_without_evidence_is_unverified_not_local(self):
        r = classify_model_contour({}, (), ["some-model"])
        self.assertEqual(r["model_contour"], "unverified")
        self.assertFalse(r["local_execution_confirmed"])

    def test_weights_hash_is_not_invented(self):
        self.assertIsNone(classify_model_contour({}, ())["weights_sha256"])


if __name__ == "__main__":
    unittest.main()
