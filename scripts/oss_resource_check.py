"""Ресурсная проверка OSS-детектора (ревизия 2, K2.4) — ОТДЕЛЬНО от офлайн-verify.

Нужны веса (`python3 scripts/oss_fetch.py`) и окружение из lock (`runtime`):
    nix-build -o venvs/oss_nix_env -E 'with import <nixpkgs> {}; python3.withPackages (p: [p.onnxruntime p.tokenizers p.numpy])'
    venvs/oss_nix_env/bin/python3 scripts/oss_resource_check.py --out artifacts/checks/revision2/oss_resource_check.json

Проверяет: (1) SHA-256 всех файлов весов = lock; (2) версии onnxruntime/tokenizers/numpy и
Python = lock; (3) каждый текст кэша оценок пересчитывается живым ONNX-выводом: |Δscore| ≤
1e-4 и та же сторона порога 0.5; (4) мета кэша ссылается на те же хеши весов. Код 0 — только
если всё совпало. Без весов/рантайма — код 2 и явная причина (ресурс недоступен ≠ измерено).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOCK = ROOT / "configs/oss/deberta-v3-base-prompt-injection-v2.lock.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tol", type=float, default=1e-4)
    a = ap.parse_args(argv)
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    cache = json.loads((ROOT / lock["score_cache"]).read_text(encoding="utf-8"))
    res: dict = {
        "lock": str(LOCK.relative_to(ROOT)),
        "cache": lock["score_cache"],
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": {},
    }
    try:
        import numpy
        import onnxruntime
        import tokenizers
    except ImportError as exc:
        res.update(ok=False, unavailable=f"runtime: {exc}")
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print("UNAVAILABLE", res["unavailable"])
        return 2
    rt = lock["runtime"]["packages"]
    vers = {
        "onnxruntime": onnxruntime.__version__,
        "tokenizers": tokenizers.__version__,
        "numpy": numpy.__version__,
        "python": platform.python_version(),
    }
    res["checks"]["runtime_versions_match_lock"] = {
        "ok": all(vers[k] == rt[k]["version"] for k in rt)
        and vers["python"] == lock["runtime"]["python"],
        "found": vers,
    }
    mdir = Path(os.environ.get("DARP_OSS_MODEL_DIR") or ROOT / lock["model_dir_default"])
    shas = {}
    for rel in lock["files"]:
        p = mdir / Path(rel).name
        shas[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    res["checks"]["weights_sha256_match_lock"] = {
        "ok": all(shas[k] == v for k, v in lock["files"].items()),
        "found": shas,
    }
    res["checks"]["cache_meta_matches_lock"] = {
        "ok": cache["meta"]["files_sha256"] == lock["files"]
        and cache["meta"]["revision"] == lock["revision"]
    }
    if not res["checks"]["weights_sha256_match_lock"]["ok"]:
        res.update(
            ok=False, unavailable="weights missing or sha256 mismatch (scripts/oss_fetch.py)"
        )
    else:
        os.environ["DARP_OSS_MODE"] = "onnx"
        from darp_range.guard_plugins.oss_deberta import onnx_score

        diffs, flips, lat = [], [], []
        thr = lock["threshold"]
        for k, row in cache["scores"].items():
            s, _, lat_new = onnx_score(row["text"])
            diffs.append(abs(s - row["score"]))
            lat.append(lat_new)
            if (s >= thr) != (row["score"] >= thr):
                flips.append(k)
        res["checks"]["scores_recomputed"] = {
            "ok": max(diffs) <= a.tol and not flips,
            "texts": len(diffs),
            "max_abs_diff": max(diffs),
            "threshold_flips": flips,
            "latency_ms_median": sorted(lat)[len(lat) // 2],
        }
        res["ok"] = all(c["ok"] for c in res["checks"].values())
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for k, c in res["checks"].items():
        print("PASS" if c["ok"] else "FAIL", k)
    print("oss_resource_check:", "PASS" if res.get("ok") else "FAIL")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
