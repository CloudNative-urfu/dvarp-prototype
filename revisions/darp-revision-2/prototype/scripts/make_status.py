"""Сборка artifacts/status.json и копии доказательств verify в artifacts/checks/integrate_verify.

Источник статусов ключевых требований — key_requirements.json прогона verify_e2e (приёмка по
файлам прогона); статусы остальных требований и пояснения заданы ниже вручную по результатам
стадий build и integrate (см. progress.md, artifacts/components/*.md). Упрощения берутся из
artifacts/simplifications.json целиком.

Запуск: `python3 scripts/make_status.py --verify runs/verify`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Ревизия 1: доказательства исправленной версии — в отдельном каталоге; прежние
# (artifacts/checks/integrate_verify/) сохраняются как исторические и не перезаписываются.
DEST = ROOT / "artifacts" / "checks" / "revision2_verify"

# Что копируется из каталога прогона (полные журналы событий не копируются: ~150 МБ).
RUN_FILES = ["summary.json", "report.md", "contour.json", "artifacts_index.json",
             "seed_spread.json", "seed_spread.csv",
             "seed-7/run/manifest.json", "seed-7/run/run.json", "seed-7/metrics",
             "seed-8/metrics", "seed-9/metrics"]

C = "artifacts/components/"
V = "artifacts/checks/revision1_verify/"

# id -> (status, [evidence...], note). Для ключевых требований статус дополнительно
# сверяется с приёмкой: провал приёмки даёт broken.
REQ: dict[str, tuple[str, list[str], str]] = {
    "FR-1.1": ("implemented", [C + "channel.md", "darp_range/channel", V + "logs/probe_channel.log"],
               "Защищаемая и атакующая стороны — отдельные пакеты, связанные только сессией канала."),
    "FR-1.2": ("implemented", [], "Единственный текстовый канал; приёмка по журналам сквозного прогона."),
    "FR-1.3": ("simplified", [], "Барьер интерфейса проверяется по событиям атакующей стороны и по импортам; research-mode маркирован и не смешивается с основным прогоном. Ревизия 2 (K2.7): simplified — барьер на уровне API и аудита в одном процессе, изоляции процессов нет; новых доказательств для повышения нет."),
    "FR-1.4": ("implemented", [C + "channel.md", C + "attackers.md", V + "logs/probe_attackers.log"],
               "Режимы D и A подключаются за одним интерфейсом сессии канала без изменения агента."),
    "FR-1.5": ("implemented", [], "Единый контракт GuardPlugin; 4 встроенных плагина и выключенные адаптеры OSS-детекторов."),
    "FR-1.6": ("implemented", [], "Вход плагина: текст, фрагменты контекста с происхождением, доверенная задача, неисполненный вызов."),
    "FR-1.7": ("implemented", [], "Сигнал allow/flag/block + score и запись затрат (cpu_ms, latency_ms, токены) на каждый вызов."),
    "FR-1.8": ("implemented", [], "Плагин не получает доступа к БД (монитор хоста), реакцию выбирает политика, исполняет код."),
    "FR-1.9": ("implemented", [], "Агент не импортирует гвардрейлы/политику/конфигурации; один хеш кода на весь прогон."),
    "FR-1.10": ("implemented", [C + "state_store.md", C + "agent.md", "darp_range/state_store", V + "logs/unit_tests.log"],
                "Синтетическая SQLite-БД магазина, сценарий возвратов; платежей нет."),
    "FR-1.11": ("implemented", [C + "tools.md", "darp_range/tools", V + "logs/probe_tools.log"],
                "Ровно пять инструментов поверх исполнителя; приёмка FR-1.9 сверяет один и тот же набор во всех конфигурациях с агентом."),
    "FR-1.12": ("implemented", [C + "simple_baseline.md", "darp_range/simple_baseline", V + "logs/probe_simple_baseline.log", V + "e2e/seed-7/metrics"],
                "g_simple: структурированная форма и правила без свободного LLM-агента, участвует во всех сравнениях."),
    "FR-1.13": ("implemented", [], "Исполнитель отклоняет чужие заказы, превышение суммы, повторные операции и при g0 без гвардрейлов."),
    "FR-1.14": ("implemented", [], "Одна граница исполнителя (один хеш) во всех конфигурациях; не перебирается."),
    "FR-1.15": ("implemented", [], "Дискретно-событийная очередь: конечное число операторов, явное время обслуживания 4/8/15 мин; предрегистрированные режимы нагрузки ×3 и деградации ревьюера (усталость удлиняет разбор и даёт вероятность пропуска атаки — параметры-допущения)."),
    "FR-1.16": ("simplified", [], "Общая очередь для атак и легального потока. ΔQ и L* режимов очереди (slow/c1, base/c1/x3, degraded/c1/x3) — в основной таблице L*. Ревизия 2 (K2.8): приёмка проверяет причинный механизм очереди на контролируемой фикстуре (manual_queue.queue_mechanism_check), эмпирический ΔQ — информационно, ноль допустим. Simplified: эффект измерим только при сжатии прибытий/деградации и получен на заглушке."),
    "FR-1.17": ("implemented", [], "Журнал с хеш-цепочками: события и затраты по компонентам (агент, гвардрейлы, исполнитель, политика, инструменты, очередь, атакующий)."),
    "FR-1.18": ("implemented", [], "Оценщик сверяет хеш и содержимое БД с ground truth, текст агента не читает (проверено импортами и пробой)."),
    "FR-1.19": ("implemented", [], "Исходы атаки только executed/refused/ignored, все три встречаются в сквозном прогоне."),
    "FR-1.20": ("simplified", [C + "attackers.md", "darp_range/attackers", V + "logs/probe_attackers.log", "artifacts/checks/revision/live_mode_a_summary.json", "artifacts/checks/report_rev1/live_fixed_summary.json"],
                "Режим D — корпус; режим A — агент с памятью, лимитом и обратной связью только из канала. Офлайн A — на детерминированной заглушке; ревизия 1: режим A поддержан в живом report_cli на общем счётчике (живой прогон 29/60 запросов), атакующий использует ту же модель Qwen, что и агент (упрощение). Стадия report: лимит «запросов на эпизод» в живом прогоне считается по исполнению эпизода (ScopedMeter), run_command режима D — 40/60 запросов без отказов по лимиту."),
    "FR-1.21": ("simplified", [C + "corpus.md", "corpus/MANIFEST.json", "corpus/darp_corpus_v1.jsonl", "corpus/holdout_v2/MANIFEST.json", "artifacts/checks/corpus_check.log"],
                "Три оси размечены; корпус v1 — 24 пары собственных синтетических текстов. Ревизия 1: holdout v2 — 19 пар перефраз экспериментальной моделью (происхождение и хеш в манифесте, заморожен до оценки; не слепой)."),
    "FR-1.22": ("simplified", [C + "corpus.md", "corpus/darp_corpus_v1.jsonl", "artifacts/checks/corpus_check.log"],
                "Для каждой атаки — парный чистый контроль; сопоставимость задаётся числом реплик и длиной, а не измеренным объёмом обработки."),
    "FR-1.23": ("implemented", [C + "metrics.md", V + "e2e/seed-7/metrics", V + "e2e/report.md"],
                "Безопасность, полезность (в т.ч. доля выполненных легальных с атаками и без, исчерпание сценария отдельно), непрерывность, детекция (TP/FP/FN/TN, PPV) и экономика по каждой конфигурации; ревизия 1: статус unresolved при статистически неразрешённом ΔV. Ревизия 2: временные метрики StepShield (s_r, s_d, IG, EIR_k), кластерный bootstrap L*, бета-биномиальный гейт и сверка τ* (metrics/robust.py, metrics/temporal.py)."),
    "FR-1.24": ("implemented", [], "L* по §9.2 для каждой конфигурации, на границах диапазонов цен, явные статусы при V(g0) − V(g) ≤ 0; независимый пересчёт в приёмке. Ревизия 1: unresolved при ΔV в пределах 95% ДИ/шума seed; приёмка на seeds 7, 8, 9 падает при уверенном выводе на неразрешённом ΔV."),
    "NFR-2.1": ("implemented", [C + "state_store.md", C + "runner.md", "darp_range/state_store", V + "logs/probe_runner.log"],
                "Снимок и восстановление БД перед каждым эпизодом (приёмка NFR-2.8 считает восстановления)."),
    "NFR-2.2": ("simplified", [C + "repro.md", "darp_range/repro", V + "e2e/seed-7/run/manifest.json", "configs/oss/deberta-v3-base-prompt-injection-v2.lock.json"],
                "Seed, хеши корпуса, конфигураций, кода и версии модели в манифесте прогона; ревизия 2: OSS-детектор закреплён ревизией и SHA-256 весов. Simplified (K2.7): живая модель внешняя, хеш весов недоступен, траектории при temperature 0 расходятся (дефекты воспроизводимости в живом срезе)."),
    "NFR-2.3": ("implemented", [C + "runner.md", V + "logs/probe_runner.log", V + "e2e/summary.json", "artifacts/checks/report_rev1/live_fixed/live.json"],
                "Повтор первого блока при тех же условиях; расхождение траекторий фиксируется как дефект (на заглушке 0; живая Qwen при temperature 0 и seed 7 — 4 дефекта в run_command стадии report)."),
    "NFR-2.4": ("simplified", [C + "guard_plugins.md", "darp_range/guard_plugins", "artifacts/guard_plugins", "artifacts/checks/revision/oss_detector_eval.json"],
                "Встроенные офлайн-плагины в сквозном прогоне. Ревизия 1: локальный OSS-детектор protectai/deberta-v3-base-prompt-injection-v2 (184M, Apache-2.0, ONNX/CPU, ревизия закреплена) измерен на уровне детекции (v1 TPR 0.83/FPR 0.38; holdout v2 0.79/0.42), Ревизия 2: адаптер oss_deberta и конфигурация g_in_oss в lock v3 и основной таблице L* (офлайн — воспроизведение кэша оценок с проверкой ревизии и SHA-256 весов; живой ONNX — отдельная ресурсная проверка scripts/oss_resource_check.py). Simplified: офлайн-verify не исполняет модель, а воспроизводит записанные оценки; LLM-судьи нет."),
    "NFR-2.5": ("simplified", [C + "report_cli.md", C + "llm.md", V + "e2e/contour.json", "artifacts/checks/revision/live_contour.json", "artifacts/checks/report_rev1/live_fixed/contour.json"],
                "Офлайн-контур локальный (запрет сокетов, аудит импортов, версии закреплены). Ревизия 1: флаг контура живой модели определяется по фактическому исполнителю (contour.model_execution): предоставленная Qwen идёт через прокси к внешнему провайдеру (Darkbloom, ошибки OpenRouter) → external, local_contour_flag=false; хеш весов недоступен и не придумывается."),
    "NFR-2.6": ("simplified", [C + "llm.md", "darp_range/llm"],
                "Проверка размера модели по заявленному числу параметров (fail-closed при неизвестном). Предоставленная живая модель qwen3.5-9b — 9B, выше ориентира 8B (в пределах исключения до 12B); детекторы-модели по умолчанию выключены."),
    "NFR-2.7": ("implemented", [C + "corpus.md", C + "agent.md", "artifacts/checks/corpus_check.log"],
                "Сценарий, корпус, промпты, правила и сообщения на английском (dirty English проверяется эвристикой), документация на русском."),
    "NFR-2.8": ("implemented", [], "Парные потоки с атакующим трафиком и без при одном снимке и seed потока; легальные траектории в паре совпадают."),
    "NFR-2.9": ("implemented", [], "Затраты атакующей стороны учитываются только у атакующего; self-limited отдельно и не входит в ΔNet/L*."),
    "NFR-2.10": ("implemented", [C + "configs.md", "configs/configs.lock.json", V + "logs/configs_frozen.log"],
                 "Конфигурации и пороги заморожены по хешу; дрейф отвергает прогон; post_hoc отмечается отдельно."),
    "NFR-2.11": ("implemented", [C + "attackers.md", C + "channel.md", V + "logs/probe_channel.log", V + "logs/probe_attackers.log"],
                 "Жёсткие лимиты шагов/запросов атакующего; превышение — нарушение протокола, а не отказ."),
    "NFR-2.12": ("implemented", [C + "configs.md", C + "simple_baseline.md", V + "e2e/seed-7/metrics", V + "e2e/report.md"],
                 "g0, g_proc, g_simple и 9 конфигураций с гвардрейлами (+ leave-one-out, чистый детектор, g0 на втором seed) в одном прогоне."),
    "NFR-2.13": ("implemented", [C + "state_store.md", C + "corpus.md", "artifacts/checks/state_store_check.log"],
                 "Только синтетические данные; проверка отсутствия реальных PII/номеров карт в БД."),
    "NFR-2.14": ("simplified", [C + "report_cli.md", V + "e2e/artifacts_index.json", V + "e2e/report.md"],
                 "Пп. 1–4 (код, корпус, журналы и трассы, отчёт с Pareto, L*, неэффективными) выдаются сквозным прогоном; п. 5 (статья) вне zero-версии по brief."),
}


def set_dest(name: str) -> None:
    """Каталог копии доказательств внутри artifacts/checks/ (по умолчанию revision1_verify).

    Ссылки V в REQ переписываются на новый каталог, чтобы доказательства стадии не смешивались
    с прежними копиями (их не перезаписываем)."""
    global DEST, V
    new_v = f"artifacts/checks/{name}/"
    if new_v == V:
        return
    for rid, (st, ev, note) in list(REQ.items()):
        REQ[rid] = (st, [new_v + e[len(V):] if e.startswith(V) else e for e in ev], note)
    DEST = ROOT / "artifacts" / "checks" / name
    V = new_v


def copy_evidence(verify: Path) -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    for name in ("verify.json", "verify.md", "key_requirements.json", "revision_checks.json",
                 "holdout_v2_eval.json", "revision2_checks.json", "coupling.json",
                 "mutation_confirm.json"):
        if (verify / name).exists():
            shutil.copy2(verify / name, DEST / name)
    shutil.copytree(verify / "logs", DEST / "logs")
    for sub in ("e2e", "e2e_stress", "e2e_faults", "e2e_holdout_v2"):
        for rel in RUN_FILES:
            src = verify / sub / rel
            dst = DEST / sub / rel
            if src.is_dir():
                shutil.copytree(src, dst)
            elif src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    (DEST / "README.md").write_text(
        f"# Доказательства verify ({DEST.name})\n\nКопия результатов `verify_command` "
        f"(`{verify.relative_to(ROOT) if verify.is_relative_to(ROOT) else verify}`): итог, "
        "приёмка ключевых требований, проверки ревизий 1 и 2 (revision2_checks.json, coupling.json, mutation_confirm.json), журналы шагов, сводки и метрики сквозного "
        "(seeds 7, 8, 9), нагрузочного, со сбоями гвардрейла и на holdout v2 прогонов. Прежние "
        "доказательства прежних версий — `artifacts/checks/integrate_verify/`, `artifacts/checks/revision1_verify/`. Полные журналы событий (`seed-7/run/logs`, `results`, ~150 МБ) не копируются; "
        "их воспроизводит `verify_command`.\n", encoding="utf-8")


def _to_artifacts(p: str) -> str:
    """Путь доказательства из runs/verify/... -> копия в artifacts, если она есть."""
    for prefix in ("runs/verify/",):
        if p.startswith(prefix):
            cand = V + p[len(prefix):]
            if (ROOT / cand).exists():
                return cand
            # журналы событий не копируются — ссылаемся на сводку прогона
            sub = p[len(prefix):].split("/", 1)[0]
            return V + f"{sub}/summary.json"
    return p


def _expand(paths: list[str]) -> list[str]:
    """Каталог-доказательство -> его непустые файлы (валидатор принимает только непустые файлы)."""
    out = []
    for p in paths:
        q = ROOT / p
        if q.is_dir():
            out += sorted(f.relative_to(ROOT).as_posix() for f in q.rglob("*")
                          if f.is_file() and f.stat().st_size > 0 and "__pycache__" not in f.parts)
        else:
            out.append(p)
    return out


def build_status(verify: Path) -> dict:
    kr = json.loads((verify / "key_requirements.json").read_text(encoding="utf-8"))
    handoff = json.loads((ROOT / "handoff.json").read_text(encoding="utf-8"))
    simpl = json.loads((ROOT / "artifacts" / "simplifications.json").read_text(encoding="utf-8"))
    comp_of = {}
    for c in json.loads((ROOT / "artifacts" / "decomposition.json").read_text(encoding="utf-8"))["components"]:
        for r in c["requirements"]:
            comp_of.setdefault(r, []).append(c["id"])
    out = {}
    for rid in handoff["requirements"]:
        status, ev, note = REQ[rid]
        ev = list(ev)
        if rid in kr["requirements"]:
            acc = kr["requirements"][rid]
            ev = [V + "key_requirements.json", V + "verify.md"] + \
                 [_to_artifacts(p) for p in acc["evidence"]] + \
                 [C + f"{c}.md" for c in comp_of.get(rid, [])]
            gated = [c for c in acc["checks"] if c["pass"] is not None]   # None — информационные
            n_ok = sum(bool(c["pass"]) for c in gated)
            n_info = len(acc["checks"]) - len(gated)
            note = (f"{note} Приёмка verify: {n_ok}/{len(gated)} проверок"
                    + (f" (+{n_info} информационных)." if n_info else "."))
            if not acc["pass"]:
                status = "broken"
                note += " Не прошли: " + "; ".join(c["name"] for c in acc["checks"] if c["pass"] is False)
        ev = list(dict.fromkeys(_expand(ev)))
        missing = [p for p in ev if not (ROOT / p).is_file() or (ROOT / p).stat().st_size == 0]
        if missing:
            raise SystemExit(f"{rid}: нет непустых файлов-доказательств {missing}")
        out[rid] = {"status": status, "evidence": ev, "note": note}
    return {"verify_ok": kr["ok"], "requirements": out, "simplifications": simpl}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", type=Path, default=ROOT / "runs" / "verify")
    ap.add_argument("--dest", default="revision2_verify",
                    help="имя каталога копии доказательств в artifacts/checks/")
    a = ap.parse_args(argv)
    set_dest(a.dest)
    verify = a.verify if a.verify.is_absolute() else Path.cwd() / a.verify
    copy_evidence(verify)
    st = build_status(verify)
    (ROOT / "artifacts" / "status.json").write_text(
        json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(Counter(v["status"] for v in st["requirements"].values()), "verify_ok", st["verify_ok"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
