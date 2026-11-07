"""Запись кэша оценок OSS-детектора живым ONNX-выводом (ревизия 2, K2.4).

Запускается в ресурсной среде (интерпретатор с onnxruntime/tokenizers/numpy из
`configs/oss/*.lock.json → runtime`, веса — `scripts/oss_fetch.py`). Прогоняет те же сквозные
офлайн-планы, что verify (корпус v1 seeds 7,8,9 и holdout v2 seeds 7,8,9) для `g0,g_in_oss`
в режиме `DARP_OSS_MODE=onnx`: детектор считает каждый текст, который видит в потоке
(реплики клиента, включая ответы отвечающего клиента), и дописывает оценку, CPU-время и
задержку в журнал записи. Итог — `corpus/oss_scores/deberta-pi-v2@90c9989.json` (тексты,
оценки, затраты, хеши весов, версии рантайма). Офлайн-verify воспроизводит детектор по этому
кэшу без весов; `scripts/oss_resource_check.py` пересчитывает оценки и сверяет.

    <nix-python> scripts/oss_record_scores.py [--work tmp/oss_record]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOCK = ROOT / "configs/oss/deberta-v3-base-prompt-injection-v2.lock.json"
PLANS = {
    "main_v1": ["--seeds", "7,8,9", "--configs", "g0,g_in_oss"],
    "holdout_v2": ["--corpus", "corpus/holdout_v2", "--seeds", "7,8,9", "--configs",
                   "g0,g_in_oss", "--replay", "none"],
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=ROOT / "tmp/oss_record")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--plan", choices=("rev2", "rev3"), default="rev2",
                    help="rev3: планы и корпуса оценки механизма claims (rev3_mechanism_eval), "
                         "отдельный кэш (кэш ревизии 2 не меняется)")
    a = ap.parse_args(argv)
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    plans, corpora = PLANS, ("corpus", "corpus/holdout_v2")
    out = a.out or ROOT / lock["score_cache"]
    if a.plan == "rev3":
        from scripts.rev3_mechanism_eval import REV3_CACHE, SETS
        from scripts.rev3_mechanism_eval import plans as rev3_plans
        plans = rev3_plans()
        corpora = tuple(sorted({v["corpus"] or "corpus" for v in SETS.values()}))
        out = a.out or ROOT / REV3_CACHE
    if a.work.exists():
        shutil.rmtree(a.work)
    a.work.mkdir(parents=True)
    rec = a.work / "record.jsonl"
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_LLM_")}
    env.update(DARP_OSS_MODE="onnx", DARP_OSS_RECORD=str(rec), PYTHONHASHSEED="0",
               PYTHONPATH=str(ROOT))
    runs = {}
    for name, args in plans.items():
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, "-m", "darp_range.report_cli", "offline", "--out",
                            str(a.work / name), *args], cwd=ROOT, env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (a.work / f"{name}.log").write_text(p.stdout, encoding="utf-8")
        runs[name] = {"argv": ["report_cli", "offline", *args], "rc": p.returncode,
                      "seconds": round(time.monotonic() - t0, 1)}
        print(name, "rc", p.returncode, runs[name]["seconds"], "s", flush=True)
        if p.returncode != 0:
            print(p.stdout[-3000:])
            return 1
    # Тексты записей корпуса (оба среза) для таблицы связки «уязвимость × тревога» (K2.1).
    os.environ.update(DARP_OSS_MODE="onnx", DARP_OSS_RECORD=str(rec))
    from scripts.coupling_analysis import DEBERTA_SPEC, detector_alarms
    for cdir in corpora:
        t0 = time.monotonic()
        al = detector_alarms(cdir, DEBERTA_SPEC)
        runs[f"corpus_texts:{cdir}"] = {"argv": ["coupling_analysis.detector_alarms", cdir],
                                        "rc": 0, "entries": len(al),
                                        "seconds": round(time.monotonic() - t0, 1)}
        print(cdir, "entries", len(al), flush=True)
    scores: dict[str, dict] = {}
    spread = 0.0
    for line in rec.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        cur = scores.get(r["key"])
        if cur is None:
            scores[r["key"]] = {"text": r["text"], "score": r["score"], "cpu_ms": [r["cpu_ms"]],
                                "latency_ms": [r["latency_ms"]]}
        else:
            spread = max(spread, abs(cur["score"] - r["score"]))
            cur["cpu_ms"].append(r["cpu_ms"])
            cur["latency_ms"].append(r["latency_ms"])
    rows = {k: {"text": v["text"], "score": v["score"],
                "cpu_ms": statistics.median(v["cpu_ms"]),
                "latency_ms": statistics.median(v["latency_ms"]), "calls": len(v["cpu_ms"])}
            for k, v in sorted(scores.items())}
    import onnxruntime
    import numpy
    import tokenizers
    mdir = ROOT / lock["model_dir_default"]
    meta = {
        "schema": "darp-oss-score-cache-1", "plugin_id": lock["plugin_id"], "model": lock["model"],
        "revision": lock["revision"], "threshold": lock["threshold"],
        "files_sha256": {rel: hashlib.sha256((mdir / Path(rel).name).read_bytes()).hexdigest()
                         for rel in lock["files"]},
        "lock_sha256": hashlib.sha256(LOCK.read_bytes()).hexdigest(),
        "runtime": {"python": platform.python_version(), "executable": sys.executable,
                    "onnxruntime": onnxruntime.__version__, "numpy": numpy.__version__,
                    "tokenizers": tokenizers.__version__, "machine": platform.machine(),
                    "provider": "CPUExecutionProvider"},
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs": runs, "texts": len(rows), "calls": sum(v["calls"] for v in rows.values()),
        "max_score_spread_same_text": spread,
        "note": "scores of the live ONNX model for every text the detector saw in the verify "
                "plans; cpu_ms/latency_ms are medians of the live calls and are charged on replay",
    }
    if any(meta["files_sha256"][k] != v for k, v in lock["files"].items()):
        print("weights sha256 differ from lock")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "scores": rows}, ensure_ascii=False, indent=1,
                              sort_keys=True) + "\n", encoding="utf-8")
    print(f"cache {out}: {len(rows)} texts, {meta['calls']} calls, spread {spread:.2e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
