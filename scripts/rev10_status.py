"""Ревизия 10: копия доказательств verify в artifacts/checks/revision10_verify и дополнение
artifacts/status.json.

    python3 scripts/rev10_status.py --verify runs/verify

Статусы и доказательства прежних ревизий сохраняются (status.json ревизии 9 читается как база).
Для ключевых требований статус сверяется с приёмкой нового verify (key_requirements.json):
провал приёмки даёт broken. Статусы прочих требований меняются только по таблице REV10 и только
с перечисленными файлами-доказательствами. Упрощения — artifacts/simplifications.json целиком.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = "artifacts/checks/revision10_verify/"
F = DEST + "rev10/"   # быстрая проверка change, выполненная внутри verify

# Файлы и каталоги прогона verify, копируемые как доказательства (без журналов событий).
COPY_FILES = ("verify.json", "verify.md", "key_requirements.json", "environment.json",
              "revision9_checks.json", "revision8_checks.json", "revision7_checks.json",
              "closed_revisions.json", "legacy_v1_regression.json", "primary_foundation.json",
              "revision10_checks.json")
RUN_FILES = ("summary.json", "report.md", "contour.json", "artifacts_index.json",
             "seed_spread.json", "seed_spread.csv", "seed-7/run/manifest.json",
             "seed-7/run/run.json", "seed-7/metrics", "seed-8/metrics", "seed-9/metrics")
RUNS = ("e2e", "e2e_legacy_v1")

# id -> (новый статус или None, [доказательства], примечание ревизии 10)
REV10: dict[str, tuple[str | None, list[str], str]] = {
    "FR-1.3": (None, [F + "summary.md", F + "hostile_child.json", F + "probes/probes.json",
                      "darp_range/isolation/broker_probes.py", "darp_range/isolation/ipc.py",
                      "tests/test_rev10_broker_record.py", "tests/test_rev10_broker_probes.py"],
               "Ревизия 10: запись попытки (AttemptRecord) строит брокер из своей копии канала; "
               "ребёнок присылает только self_report {tactics, believed_success}; 7 видов порчи "
               "IPC враждебным процессом под bwrap → protocol_violation/ipc_protocol_error без "
               "ошибки эпизода и без поддельных значений в записях/журнале. Доказательства "
               "изоляции собирает пробный процесс брокера под тем же argv bwrap (pid_count, "
               "/proc/*/environ, CapEff, sid/NSpid/die-with-parent со стороны хоста); мутанты "
               "MX2/MX4/MX6 убиты, MX3 эквивалентен (CapEff=0 с флагом и без). Остаётся "
               "simplified: verify по умолчанию in-process (claim_level=in_process_research), "
               "защищаемая сторона не OS-изолирована (defender→attacker_memory — convention, "
               "проба gc находит канарейку атакующего)."),
    "FR-1.14": (None, [DEST + "primary_foundation.json", DEST + "e2e/report.md"],
                "Ревизия 10: главный прогон verify и таблица L* — на основе v2r (boundary-2r), "
                "граница прогона приёмки берётся из основы прогона; v1 — отдельный legacy-шаг."),
    "FR-1.24": (None, [DEST + "e2e/report.md", DEST + "primary_foundation.json",
                       DEST + "legacy_v1_regression.json", "tests/test_rev10_v2r.py",
                       "artifacts/rev10/legacy_v1_noise_control.json"],
                "Ревизия 10 (v2r-primary-table): L* публикуется для рекомендуемой основы v2r "
                "(seeds 7/8/9) при любом исходе; приёмка проверяет правила статусов §9.2 "
                "(pays_at_threshold / no_safety_gain / more_violations / unresolved), а не знак "
                "ΔV; мутант «ΔV=0 → pays_at_threshold» роняет приёмку. Legacy v1 совпадает с "
                "таблицами кода rev9 во всех стабильных столбцах; столбцы измеренных затрат "
                "сравниваются информационно (упрощение, контроль шума legacy_v1_noise_control)."),
    "NFR-2.2": (None, ["darp_range/repro/manifest.py", "tests/test_rev10_claim_matrix.py",
                       DEST + "e2e/seed-7/run/manifest.json"],
                "Ревизия 10: схема манифеста darp.run_manifest/2 обязана нести claim_level с "
                "фактами (sandbox/probes/research/provenance/ipc_errors); без фабрики — "
                "in_process_research; манифесты /1 читаются без пересчёта отпечатков."),
    "NFR-2.9": (None, ["darp_range/isolation/sandbox.py", "tests/test_rev10_broker_record.py"],
                "Ревизия 10: событие attempt от процесса атакующего несёт cost=null; затраты "
                "модели атакующего пишет только ChatModel брокера (подделка tokens_in=1e9 → "
                "ipc_protocol_error, в журнал не попадает)."),
    "NFR-2.11": (None, ["darp_range/isolation/sandbox.py", F + "hostile_child.json"],
                 "Ревизия 10: нарушение IPC-протокола процессом атакующего — protocol_violation "
                 "с violation=ipc_protocol_error попытки, а не исключение прогона."),
}


def _expand(paths):
    out = []
    for p in paths:
        q = ROOT / p
        if q.is_dir():
            out += sorted(f.relative_to(ROOT).as_posix() for f in q.rglob("*")
                          if f.is_file() and f.stat().st_size > 0)
        else:
            out.append(p)
    return out


def copy_evidence(verify: Path) -> None:
    dest = ROOT / DEST
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for n in COPY_FILES:
        if (verify / n).exists():
            shutil.copy2(verify / n, dest / n)
    if (verify / "logs").is_dir():
        shutil.copytree(verify / "logs", dest / "logs")
    if (verify / "rev10").is_dir():
        shutil.copytree(verify / "rev10", dest / "rev10",
                        ignore=shutil.ignore_patterns("*.jsonl", "*.xz", "*.sqlite*", "run", "rt"))
    for sub in RUNS:
        for rel in RUN_FILES:
            src, dst = verify / sub / rel, dest / sub / rel
            if src.is_dir():
                shutil.copytree(src, dst)
            elif src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    (dest / "README.md").write_text(
        "# Доказательства verify ревизии 10\n\nКопия результатов `verify_command` (итог, приёмка "
        "ключевых требований, проверки закрытых ревизий, шаги ревизии 10, сводки и метрики "
        "главного прогона v2r `e2e/` и legacy v1 `e2e_legacy_v1/`). Полные журналы событий не "
        "копируются; их воспроизводит `verify_command`.\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", type=Path, default=ROOT / "runs" / "verify")
    a = ap.parse_args(argv)
    verify = a.verify if a.verify.is_absolute() else Path.cwd() / a.verify
    copy_evidence(verify)
    st_path = ROOT / "artifacts" / "status.json"
    st = json.loads(st_path.read_text(encoding="utf-8"))
    kr = json.loads((verify / "key_requirements.json").read_text(encoding="utf-8"))
    handoff = json.loads((ROOT / "handoff.json").read_text(encoding="utf-8"))
    reqs = st["requirements"]
    for rid in handoff["requirements"]:
        r = reqs[rid]
        if rid in REV10:
            new, ev, note = REV10[rid]
            r["evidence"] = list(dict.fromkeys(r["evidence"] + _expand(ev)))
            r["note"] = f"{r['note']} {note}"
            if new:
                r["status"] = new
        if rid in kr["requirements"]:
            acc = kr["requirements"][rid]
            gated = [c for c in acc["checks"] if c["pass"] is not None]
            n_ok = sum(bool(c["pass"]) for c in gated)
            r["evidence"] = list(dict.fromkeys([DEST + "key_requirements.json", DEST + "verify.md"]
                                               + r["evidence"]))
            r["note"] += f" Ревизия 10 integrate: приёмка verify {n_ok}/{len(gated)}."
            if not acc["pass"]:
                r["status"] = "broken"
                r["note"] += " Не прошли: " + "; ".join(
                    c["name"] for c in acc["checks"] if c["pass"] is False)
        missing = [p for p in r["evidence"] if not (ROOT / p).is_file()
                   or (ROOT / p).stat().st_size == 0]
        if missing:
            raise SystemExit(f"{rid}: нет непустых файлов-доказательств {missing}")
    st["verify_ok"] = kr["ok"]
    st["simplifications"] = json.loads(
        (ROOT / "artifacts" / "simplifications.json").read_text(encoding="utf-8"))
    st_path.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(Counter(v["status"] for v in reqs.values()), "verify_ok", st["verify_ok"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
