"""Ревизия 11: копия доказательств verify в artifacts/checks/revision11_verify и сборка
artifacts/status.json.

    python3 scripts/rev11_status.py --verify runs/verify

База — status.json конца ревизии 10 (`artifacts/history_rev10/status.json`), поэтому повторный
запуск не дублирует примечания. Для ключевых требований статус сверяется с приёмкой нового
verify (`key_requirements.json`): провал приёмки даёт broken. Статусы прочих требований меняются
только по таблице REV11 и только с перечисленными файлами-доказательствами. Упрощения —
artifacts/simplifications.json целиком.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/history_rev10/status.json"
DEST = "artifacts/checks/revision11_verify/"
R = "artifacts/rev11/"
COMP = "artifacts/components/shop_contour.md"

COPY_FILES = ("verify.json", "verify.md", "key_requirements.json", "environment.json",
              "closed_revisions.json", "revision9_checks.json", "legacy_v1_regression.json",
              "primary_foundation.json")
COPY_DIRS = {"rev11": ("summary.json", "summary.md", "smoke/smoke.json", "mutants/mutants.json"),
             "rev10": ("summary.json", "summary.md")}

# id -> (новый статус или None, [доказательства], примечание ревизии 11)
REV11: dict[str, tuple[str | None, list[str], str]] = {
    "FR-1.3": (None, [DEST + "rev11/summary.json", "tests/test_rev11_broker_fail_closed.py",
                      "darp_range/isolation/sandbox.py", "darp_range/runner/orchestrator.py"],
               "Ревизия 11 (broker-fail-closed): ожидание строки IPC ограничено тайм-аутом "
               "(select на stdout) — зависший ребёнок даёт ipc_protocol_error попытки и kill "
               "группы процессов, прогон продолжается; stderr ребёнка — кольцевой буфер 64 КиБ со "
               "счётчиком отброшенного; Runner live+llm без фабрики и без in_process_research → "
               "RunnerError до первого запроса; report_cli live A по умолчанию — фабрика bwrap. "
               "Мутанты M8, M9 убиты. Статус не меняется: защищаемая сторона по-прежнему не "
               "OS-изолирована."),
    "FR-1.10": (None, [COMP, R + "logs/lifecycle.log", DEST + "rev11/smoke/smoke.json",
                       "darp_range/shop_svc/service.py", "darp_range/pay_emu/server.py",
                       "darp_range/shop_svc/worker.py", "scripts/shop_slice.py"],
                "Ревизия 11 (returns-operator-payout-slice): локальный контур возврата на "
                "реальных процессах 127.0.0.1 — shop-svc (операторские approve/reject, клиентский "
                "статус), payout-worker (outbox выплат), pay-emu (своя синтетическая книга, "
                "Idempotency-Key, сбои из файла оценщика). Сквозной путь без LLM: high_value → пять "
                "инструментов (исполнитель v2r) → серверная эскалация → approve оператора по HTTP → "
                "выплата → клиент видит refunded. Возврат в БД магазина отделён от выплаты; деньги "
                "и провайдеры синтетические."),
    "FR-1.13": (None, ["darp_range/shop_svc/service.py", "darp_range/shop_svc/auth.py",
                       DEST + "rev11/mutants/mutants.json", DEST + "rev11/smoke/smoke.json"],
                "Ревизия 11 (operator-decision-service): единственный путь записи согласования — "
                "аутентифицированный оператор (HMAC-токен контура; роль/operator_id в теле "
                "игнорируются → 403) с перепроверкой статуса escalated, владельца позиции, R1, R3 "
                "(amount ≤ price×qty), R4 и final sale в одной транзакции BEGIN IMMEDIATE; без "
                "токена 401, нарушения 409 — логический хеш БД не меняется; два параллельных "
                "approve → одна заявка; повтор с тем же Idempotency-Key → сохранённый ответ. "
                "Мутанты M1 (роль из тела), M2 (без R3) убиты. Исполнитель агента и пять "
                "инструментов не менялись."),
    "FR-1.17": (None, [DEST + "rev11/smoke/smoke.json", "darp_range/svckit/http.py"],
                "Ревизия 11: каждый HTTP-запрос сервисов контура пишется JSONL-строкой с "
                "корреляционными полями (ticket_id, refund_request_id, idempotency_key); трассы "
                "каждого smoke-сценария копируются в artifacts/rev11/smoke/<сценарий>/traces/."),
    "FR-1.18": (None, ["scripts/shop_slice_oracle.py", DEST + "rev11/smoke/smoke.json",
                       "tests/test_rev11_shop_slice.py"],
                "Ревизия 11: независимый oracle контура (отдельный процесс, mode=ro, без импорта "
                "кода проекта) судит каждый сценарий по БД контура и книге эмулятора: Σ succeeded ≤ 1 "
                "на заявку, сумма = заявке, получатель = payee платежа, клиент не видел refunded "
                "без succeeded, у каждой завершённой эскалации есть решение оператора; "
                "отрицательный контроль — подложенная вторая succeeded-строка в копии книги → FAIL."),
    "NFR-2.1": (None, [DEST + "rev11/smoke/smoke.json", R + "logs/lifecycle.log"],
                "Ревизия 11: `shop_slice.py reset` пересоздаёт БД контура и книгу из seed; "
                "логический хеш равен эталону (сценарий reset_deterministic); restart shop-svc / "
                "pay-emu / kill -9 payout-worker не теряют решений и выплат."),
    "NFR-2.5": (None, [COMP, R + "logs/lifecycle.log", "tests/test_rev11_shop_slice.py"],
                "Ревизия 11: внешняя зависимость (платёжный эмулятор) — отдельный локальный процесс "
                "за границей HTTP на 127.0.0.1; занятый порт или неработающий loopback → явная "
                "ошибка (код 3), а не пропуск и не зависание (исправлено на стадии integrate)."),
    "NFR-2.13": (None, [COMP, "darp_range/pay_emu/ledger.py"],
                 "Ревизия 11: книга эмулятора синтетическая (charge_id/payee_ref 'pm_syn_<order>' "
                 "по доставленным заказам синтетической БД), без реальных провайдеров, реквизитов "
                 "и денег; получатель в запросе запрещён (400), мутант M5 убит."),
}


def _expand(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        q = ROOT / p
        if q.is_dir():
            out += sorted(f.relative_to(ROOT).as_posix() for f in q.rglob("*")
                          if f.is_file() and f.stat().st_size > 0 and "__pycache__" not in f.parts)
        else:
            out.append(p)
    return out


def copy_evidence(verify: Path) -> None:
    dest = ROOT / DEST
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in COPY_FILES:
        if (verify / name).exists():
            shutil.copy2(verify / name, dest / name)
    for sub, files in COPY_DIRS.items():
        for f in files:
            src = verify / sub / f
            if src.exists():
                (dest / sub / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / sub / f)
    logs = verify / "logs"
    if logs.is_dir():
        shutil.copytree(logs, dest / "logs")
    for run in ("e2e", "e2e_legacy_v1"):
        for f in ("summary.json", "report.md", "contour.json"):
            src = verify / run / f
            if src.exists():
                (dest / run).mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / run / f)
    (dest / "README.md").write_text(
        "# Доказательства verify (ревизия 11)\n\nКопия результатов `verify_command` из чистой "
        f"копии рабочего каталога (`{verify}`): итог (`verify.json`, `verify.md`), приёмка ключевых "
        "требований (`key_requirements.json`), журналы шагов (`logs/`), быстрая проверка change "
        "rev11 (`rev11/summary.*`, smoke-матрица и мутанты), регрессия rev10 (`rev10/summary.*`), "
        "сводки главного (v2r) и legacy-v1 прогонов. Полные журналы событий не копируются — их "
        "воспроизводит `verify_command`.\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", type=Path, default=ROOT / "runs" / "verify")
    a = ap.parse_args(argv)
    verify = a.verify if a.verify.is_absolute() else Path.cwd() / a.verify
    copy_evidence(verify)
    st = json.loads(BASE.read_text(encoding="utf-8"))
    kr = json.loads((verify / "key_requirements.json").read_text(encoding="utf-8"))
    vj = json.loads((verify / "verify.json").read_text(encoding="utf-8"))
    handoff = json.loads((ROOT / "handoff.json").read_text(encoding="utf-8"))
    reqs = st["requirements"]
    for rid in handoff["requirements"]:
        r = reqs[rid]
        if rid in REV11:
            new, ev, note = REV11[rid]
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
            r["note"] += f" Ревизия 11 integrate: приёмка verify из чистой копии {n_ok}/{len(gated)}."
            if not acc["pass"]:
                r["status"] = "broken"
                r["note"] += " Не прошли: " + "; ".join(
                    c["name"] for c in acc["checks"] if c["pass"] is False)
        missing = [p for p in r["evidence"] if not (ROOT / p).is_file()
                   or (ROOT / p).stat().st_size == 0]
        if missing:
            raise SystemExit(f"{rid}: нет непустых файлов-доказательств {missing}")
    st["verify_ok"] = bool(vj["ok"])
    st["last_verification"] = {
        "revision": 11, "stage": "integrate", "date": "2026-11-10", "ok": bool(vj["ok"]),
        "steps_ok": vj.get("steps_ok"), "key_requirements_ok": vj.get("key_requirements_ok"),
        "seconds": vj.get("seconds"), "evidence": DEST + "verify.json",
        "steps": f"{sum(bool(s.get('ok')) for s in vj.get('steps', []))}/{len(vj.get('steps', []))}",
        "failed_steps": [s.get("step") for s in vj.get("steps", []) if not s.get("ok")],
        "command": ["python3", "scripts/verify_e2e.py", "--out", "runs/verify"],
        "copy": "clean copy (rsync без runs/, .runtime, кэшей) в .runtime/clean_rev11/work",
        "log": DEST + "clean_verify.log"}
    st["simplifications"] = json.loads(
        (ROOT / "artifacts" / "simplifications.json").read_text(encoding="utf-8"))
    (ROOT / "artifacts" / "status.json").write_text(json.dumps(st, ensure_ascii=False, indent=1),
                                                    encoding="utf-8")
    from collections import Counter
    print(Counter(v["status"] for v in reqs.values()), "verify_ok", st["verify_ok"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
