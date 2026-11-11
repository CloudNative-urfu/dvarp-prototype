"""Стадия report ревизии 11: добавить в artifacts/status.json доказательства свежего verify из
чистой копии (artifacts/checks/report_stage_verify/) и итог проверки контроллера (handoff.verification).

    python3 scripts/report_stage_status.py

Статусы не повышаются: для ключевых требований статус сверяется с приёмкой свежего verify
(провал → broken), прочие не меняются. Повторный запуск не дублирует доказательства и примечания.
Офлайн, без сети и модельных API.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = "artifacts/checks/report_stage_verify/"
MARK = "Стадия report:"


def main() -> int:
    st_path = ROOT / "artifacts" / "status.json"
    st = json.loads(st_path.read_text(encoding="utf-8"))
    kr = json.loads((ROOT / DEST / "key_requirements.json").read_text(encoding="utf-8"))
    vj = json.loads((ROOT / DEST / "verify.json").read_text(encoding="utf-8"))
    handoff = json.loads((ROOT / "handoff.json").read_text(encoding="utf-8"))
    reqs = st["requirements"]
    for rid in handoff["requirements"]:
        r = reqs[rid]
        r["note"] = r["note"].split(" " + MARK)[0]
        if rid in kr["requirements"]:
            acc = kr["requirements"][rid]
            gated = [c for c in acc["checks"] if c["pass"] is not None]
            n_ok = sum(bool(c["pass"]) for c in gated)
            r["evidence"] = list(dict.fromkeys([DEST + "key_requirements.json", DEST + "verify.md"]
                                               + r["evidence"]))
            r["note"] += (f" {MARK} повторный verify из чистой копии (2026-11-10, после integrate, "
                          f"код не менялся) — приёмка {n_ok}/{len(gated)}.")
            if not acc["pass"]:
                r["status"] = "broken"
                r["note"] += " Не прошли: " + "; ".join(
                    c["name"] for c in acc["checks"] if c["pass"] is False)
        missing = [p for p in r["evidence"] if not (ROOT / p).is_file()
                   or (ROOT / p).stat().st_size == 0]
        if missing:
            raise SystemExit(f"{rid}: нет непустых файлов-доказательств {missing}")
        if r["status"] not in ("implemented", "simplified", "missing", "broken", "out_of_scope"):
            raise SystemExit(f"{rid}: недопустимый статус {r['status']}")
    steps = vj.get("steps", [])
    st["verify_ok"] = bool(vj["ok"])
    st["last_verification"] = {
        "revision": 11, "stage": "report", "date": "2026-11-10", "ok": bool(vj["ok"]),
        "steps_ok": vj.get("steps_ok"), "key_requirements_ok": vj.get("key_requirements_ok"),
        "seconds": vj.get("seconds"), "evidence": DEST + "verify.json",
        "steps": f"{sum(bool(s.get('ok')) for s in steps)}/{len(steps)}",
        "failed_steps": [s.get("step") for s in steps if not s.get("ok")],
        "command": ["sh", "scripts/isolated_verify.sh", "runs/verify"],
        "copy": "clean copy (без runs/, .runtime, кэшей) в .runtime/clean_report/work",
        "log": DEST + "clean_verify.log",
        "controller": handoff.get("verification"),
        "previous": "artifacts/checks/revision11_verify/verify.json (integrate, PASS 3326.7 s)"}
    st["simplifications"] = json.loads(
        (ROOT / "artifacts" / "simplifications.json").read_text(encoding="utf-8"))
    st_path.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    print(Counter(v["status"] for v in reqs.values()), "verify_ok", st["verify_ok"],
          "simplifications", len(st["simplifications"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
