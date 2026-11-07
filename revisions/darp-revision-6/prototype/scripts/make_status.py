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
    "FR-1.4": ("implemented", [C + "channel.md", C + "attackers.md", V + "logs/probe_attackers.log", "darp_range/channel/proposal_contract.py", "tests/test_revision5_confirm.py", "artifacts/checks/revision5/blind_eval.json", "artifacts/checks/revision5/confirm_probe_OLD.json", "artifacts/checks/revision5/confirm_probe_NEW.json", "artifacts/checks/revision5/mutation_confirm_rev5.json"],
               "Режимы D и A подключаются за одним интерфейсом сессии канала без изменения агента. Ревизия 5 (confirm-allowlist): легальный клиент режима D говорит «yes» только если каждая клауза реплики агента целиком сопоставлена закрытому шаблону (позитивная грамматика вместо словаря NEUTRAL); восстановленные пробы критика rev4 7/7 не «yes» (на rev4 — 7/7 «yes»), вставки слов бывшего NEUTRAL 0/1264 «yes», мутации 15/15; слепой набор другой сессии: ложных «yes» 0/38, но «yes» на корректных лишь 12/37 (полезность — остаток)."),
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
    "FR-1.19": ("implemented", ["artifacts/checks/revision3/mechanism/rev3_mechanism.md", "artifacts/checks/revision3/live_mode_a/results.json", "artifacts/checks/revision5/live_A/summary.json", "artifacts/checks/revision5/live_A/run1/results.json", "artifacts/checks/revision5/live_A/run2/results.json", "tests/test_revision5_live_interleaved.py", "artifacts/checks/revision5/regressions/live_interleaved_OLD.log"], "Исходы атаки только executed/refused/ignored, все три встречаются в сквозном прогоне. Ревизия 3 (K3.2e): аудит окончаний сессий атак — ранних user_ended 0 на всех наборах; живой режим A: все исполнения — в сессиях, закрытых защитником. Ревизия 5 (live-a-interleaved): статус измерения пишется прямо в сырые строки results.json живого режима A (measured / truncated_* / not_measured_*); попытка, которой не было из-за бюджета, получает outcome=null, а не ignored (исход оценщика по БД сохраняется в evaluator_outcome); RED на скрипте rev4, GREEN 4/4."),

    "FR-1.20": ("simplified", ["artifacts/preregistration-rev4.json", "artifacts/revision-4-budget.json", "artifacts/checks/revision3/live_mode_a/results.json", "artifacts/revision-3-budget.json", C + "attackers.md", "darp_range/attackers", V + "logs/probe_attackers.log", "artifacts/checks/revision/live_mode_a_summary.json", "artifacts/checks/report_rev1/live_fixed_summary.json", "artifacts/preregistration-rev5.json", "artifacts/preregistration-rev5-live.json", "artifacts/improvement-budget.json", "artifacts/checks/revision5/live_A/summary.json", "artifacts/checks/revision5/starvation_OLD.json", "artifacts/checks/revision5/starvation_NEW.json"],
                "Режим D — корпус; режим A — агент с памятью, лимитом и обратной связью только из канала. Офлайн A — на детерминированной заглушке; ревизия 1: режим A поддержан в живом report_cli на общем счётчике (живой прогон 29/60 запросов), атакующий использует ту же модель Qwen, что и агент (упрощение). Стадия report: лимит «запросов на эпизод» в живом прогоне считается по исполнению эпизода (ScopedMeter), run_command режима D — 40/60 запросов без отказов по лимиту. Ревизия 3 (K3.2d): предрегистрированный живой режим A (scripts/live_mode_a_v.py) против g0 и g_in на heldout_v3: V(g0)=3/5, V(g_in)=1/6, ΔV 0.43 [−0.11; 0.75] (n мал, не разрешён); g_in×atk-01 исполнена без тревоги правил. Ревизия 4: два предрегистрированных живых прогона режима A (heldout_v3 atk-06/08/11, g0/g_in) с записью кассет и офлайн-воспроизведением (artifacts/checks/revision4/live_A); описательно, без подтверждающего вывода. Ревизия 5: чередующийся дизайн живого A (порядок по сценарию, g_in первой в run2) и свой бюджет у каждой ячейки сценарий×конфигурация; на фейке rev4-дизайн отдаёт g_in 0 запросов, rev5 — поровну. Два живых прогона по 59 запросов: все 6 ячеек измерены, atk-08 исполнена под g0 и g_in (run1) и под g_in (run2) без тревог входного фильтра; описательно, n = 3, эффект защиты не оценивается. Simplified: n мал, провайдер внешний."),

    "FR-1.21": ("simplified", [C + "corpus.md", "corpus/MANIFEST.json", "corpus/darp_corpus_v1.jsonl", "corpus/holdout_v2/MANIFEST.json", "artifacts/checks/corpus_check.log"],
                "Три оси размечены; корпус v1 — 24 пары собственных синтетических текстов. Ревизия 1: holdout v2 — 19 пар перефраз экспериментальной моделью (происхождение и хеш в манифесте, заморожен до оценки; не слепой)."),
    "FR-1.22": ("simplified", ["corpus/heldout_v3/MANIFEST.json", "artifacts/preregistration-rev3.json", C + "corpus.md", "corpus/darp_corpus_v1.jsonl", "artifacts/checks/corpus_check.log"],
                "Для каждой атаки — парный чистый контроль; сопоставимость задаётся числом реплик и длиной, а не измеренным объёмом обработки. Ревизия 3: held-out v3 (13 пар атака/контроль той же поверхности, хеш в предрегистрации rev3)."),

    "FR-1.23": ("implemented", ["tests/test_revision4_metrics.py", "artifacts/checks/revision4/regressions/metrics_OLD.log", "artifacts/checks/revision4/regressions/metrics_NEW.log", "artifacts/checks/revision3/regressions/metrics_behaviour_NEW.json", "artifacts/checks/revision3/regressions/metrics_OLD.log", C + "metrics.md", V + "e2e/seed-7/metrics", V + "e2e/report.md", "tests/test_revision5_metrics.py", "artifacts/checks/revision5/regressions/metrics_OLD.log", "artifacts/checks/revision5/window_table_rev4verify.md"],
                "Безопасность, полезность (в т.ч. доля выполненных легальных с атаками и без, исчерпание сценария отдельно), непрерывность, детекция (TP/FP/FN/TN, PPV) и экономика по каждой конфигурации; ревизия 1: статус unresolved при статистически неразрешённом ΔV. Ревизия 2: временные метрики StepShield (s_r, s_d, IG, EIR_k), кластерный bootstrap L*, бета-биномиальный гейт и сверка τ* (metrics/robust.py, metrics/temporal.py). Ревизия 3: StepShield k=3 по умолчанию (EIR_3, EIR_3×recall, random baseline), PPV эскалаций ∈ [0,1] и τ* с предпосылками A1–A3, режимы bootstrap L*, раздельный учёт попыток отказа (R_attempted); проверено на ручных контрпримерах (tests/test_revision3_metrics.py, RED на старом коде). Ревизия 4: τ* с предпосылкой A4 (нет новых нарушений относительно g0), статусы in_scope/out_of_scope/not_applicable, обобщённое L*=C_manual/PPV_net на контрпримере критика; StepShield: явная неприменимость при терминальном нарушении (n−s_r ≤ k) вместо вырожденных EIR=1/IG=0, окно вмешательства и k по ёмкости очереди (tests/test_revision4_metrics.py, RED на старом коде). Ревизия 5: окно вмешательства — таблица по всем k ∈ {0..3} с кластерным (по сценарию) bootstrap-ДИ разности IFR−IFR_rand и статусом informative/not_informative; на данных rev4 все k not_informative, утверждение по одному k=3 из отчёта убрано."),

    "FR-1.24": ("implemented", ["artifacts/checks/revision5/confirm_impact.json"], "L* по §9.2 для каждой конфигурации, на границах диапазонов цен, явные статусы при V(g0) − V(g) ≤ 0; независимый пересчёт в приёмке. Ревизия 1: unresolved при ΔV в пределах 95% ДИ/шума seed; приёмка на seeds 7, 8, 9 падает при уверенном выводе на неразрешённом ΔV. Ревизия 3: bootstrap L* различает threshold / pays_at_any_price / sign_unstable / dV_unresolved, без уверенного вывода при смене знака; чувствительность L* к R_attempted. Ревизия 4: режим bootstrap L* уверенный только при доле ≥ 97.5% всех репликатов; смесь критика 2/2/96 → sign_unstable; новые режимы more_violations/no_safety_gain. Ревизия 5: допуск по стоимости 1e-6 USD/эпизод во фронте Pareto и доминировании (dominated_by не зависит от шума cpu_ms); confirm-impact: худшие переворачивания ответов клиента не меняют режим bootstrap L* ни в одном живом прогоне (по предрегистрированному правилу граница не измерима — записанных confirm-эпизодов со сравниваемой конфигурацией нет)."),

    "NFR-2.1": ("implemented", [C + "state_store.md", C + "runner.md", "darp_range/state_store", V + "logs/probe_runner.log"],
                "Снимок и восстановление БД перед каждым эпизодом (приёмка NFR-2.8 считает восстановления)."),
    "NFR-2.2": ("simplified", [C + "repro.md", "darp_range/repro", V + "e2e/seed-7/run/manifest.json", "configs/oss/deberta-v3-base-prompt-injection-v2.lock.json"],
                "Seed, хеши корпуса, конфигураций, кода и версии модели в манифесте прогона; ревизия 2: OSS-детектор закреплён ревизией и SHA-256 весов. Simplified (K2.7): живая модель внешняя, хеш весов недоступен, траектории при temperature 0 расходятся (дефекты воспроизводимости в живом срезе)."),
    "NFR-2.3": ("implemented", ["darp_range/llm/cassette.py", "tests/test_revision4_replay.py", "artifacts/preregistration-rev4.json", "artifacts/checks/revision4/live_A", C + "runner.md", V + "logs/probe_runner.log", V + "e2e/summary.json", "artifacts/checks/report_rev1/live_fixed/live.json", "artifacts/checks/revision5/live_A/run1/cassette.jsonl", "artifacts/checks/revision5/live_A/run2/cassette.jsonl"],
                "Повтор первого блока при тех же условиях; расхождение траекторий фиксируется как дефект (на заглушке 0; живая Qwen при temperature 0 и seed 7 — 4 дефекта в run_command стадии report). Ревизия 4: запись/воспроизведение живых траекторий (darp_range/llm/cassette.py): живой прогон пишет кассету (SHA-256 тела запроса → ответ), verify офлайн воспроизводит каждый живой прогон rev4 по кассете и сверяет исходы (replay_mode в results.json; расхождение запроса/исхода — дефект); два живых прогона одной команды и seed сравниваются, расхождения записаны как дефекты (rev4_repeat_compare.json). Повторное сэмплирование внешнего провайдера не воспроизводимо — это зафиксировано, а не скрыто. Ревизия 5: кассеты чередующихся живых прогонов воспроизводятся офлайн точно (verify rev5_live_replay_run1/run2); replay rev4 run1/run2 по-прежнему exact; дизайн (порядок, бюджет ячейки) берётся из исходного results.json."),
    "NFR-2.4": ("implemented", ["artifacts/checks/revision3/oss/oss_fetch.log", "artifacts/checks/revision3/oss/record_rev3.log", C + "guard_plugins.md", "darp_range/guard_plugins", "artifacts/guard_plugins", "artifacts/checks/revision/oss_detector_eval.json"],
                "Встроенные офлайн-плагины в сквозном прогоне. Ревизия 1: локальный OSS-детектор protectai/deberta-v3-base-prompt-injection-v2 (184M, Apache-2.0, ONNX/CPU, ревизия закреплена) измерен на уровне детекции (v1 TPR 0.83/FPR 0.38; holdout v2 0.79/0.42), Ревизия 2: адаптер oss_deberta и конфигурация g_in_oss в lock v3 и основной таблице L* (офлайн — воспроизведение кэша оценок с проверкой ревизии и SHA-256 весов; живой ONNX — отдельная ресурсная проверка scripts/oss_resource_check.py). Simplified: офлайн-verify не исполняет модель, а воспроизводит записанные оценки; LLM-судьи нет. Ревизия 3: веса скачаны по ревизии, 5/5 SHA-256 = lock, живой ONNX-вывод записал кэш rev3 (298 текстов, разброс 0) для held-out v3; повышено до implemented (согласно ресурсной проверке критика ревизии 2 и повтору здесь)."),

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


# Ревизия 6 (foundation): дополнения к статусам/доказательствам. Статусы не повышаются и не
# понижаются: boundary-1 остаётся историческим baseline, boundary-2 добавлен отдельно.
R6 = "artifacts/checks/revision6/"
REV6: dict[str, tuple[list[str], str]] = {
    "FR-1.4": (["darp_range/channel/legal_user.py", "tests/test_legal_facts_mode.py",
                R6 + "confirm_mode_compare.json", R6 + "mutations.json"],
               "Ревизия 6 (legal-client-fact-contract): новый режим клиента confirm_mode='facts' "
               "(по умолчанию для новых прогонов) — на просьбу подтвердить клиент пересказывает "
               "свои факты, токенов одобрения нет по построению (14 проб критика rev5 и 5 "
               "позитивов, генератор реплик → 0 одобрений, мутации ловятся); grammar_rev5 сохранён "
               "для replay и воспроизводит отпечатки rev5."),
    "FR-1.13": (["darp_range/executor/executor.py", "darp_range/executor/boundary.py",
                 "darp_range/state_store/trusted.py", "tests/test_server_invariants_v2.py",
                 R6 + "mutations.json", "artifacts/production-readiness.md"],
                "Ревизия 6 (server-invariants-v2): отдельная граница executor-boundary-2 "
                "(явный параметр Executor/RunPlan.foundation) дополнительно проверяет R1/R2/R5 в "
                "той же транзакции BEGIN IMMEDIATE, что и запись, по доверенной refund_policy и "
                "доверенным таблицам defect_evidence/refund_approvals (пишут только system/"
                "operator); конкурентность и TOCTOU покрыты тестами, 26/26 неэквивалентных мутаций "
                "убиты. boundary-1 не изменён (исторический baseline)."),
    "FR-1.14": (["artifacts/production-readiness.md", R6 + "boundary_diag.json",
                 "artifacts/evaluation-results.md"],
                "Ревизия 6: основа (boundary-1 или boundary-2) выбирается на весь прогон и пишется "
                "в манифест (foundation_spec) и журнал (exec_request.boundary); внутри прогона "
                "все конфигурации делят одну границу — она не перебирается вместе с гвардрейлами."),
    "FR-1.18": (["darp_range/evaluator/state.py", "tests/test_foundation_v2.py",
                 R6 + "foundation_table.md", R6 + "mutations.json"],
                "Ревизия 6 (oracle-v2): evaluator-2 засчитывает исключение final sale только по "
                "доверенной verified defect_evidence стартового состояния и не засчитывает "
                "refund_without_review при доверенном одобрении оператора на сумму ≤ max; "
                "отрицательные контроли и мутации; evaluator-1 сохранён для исторических прогонов."),
    "FR-1.19": (["scripts/live_mode_a_v.py", "tests/test_revision6_live_truncation.py",
                 R6 + "relabel/summary.json"],
                "Ревизия 6 (live-a-truncation-honesty): обрезанная попытка живого A получает "
                "outcome=null (исход по БД — в state_outcome_at_truncation); пересчёт rev4/rev5 и "
                "лога критика rev4: 0 строк ignored при measurement≠measured."),
    "NFR-2.11": (["scripts/live_mode_a_v.py", "tests/test_revision6_live_truncation.py",
                  "artifacts/preregistration-rev6.json"],
                 "Ревизия 6: protocol_violation — только собственный лимит атакующего; обрезка "
                 "бюджетом ячейки/прогона (его делят агент и атакующий) нарушением протокола "
                 "атакующего не считается; бюджет ячейки следующего живого прогона (13 запросов) "
                 "вычислен по кассетам и зафиксирован в предрегистрации rev6."),
}
for _rid, (_ev, _note) in REV6.items():
    _st, _e, _n = REQ[_rid]
    REQ[_rid] = (_st, _e + _ev, f"{_n} {_note}")


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
                 "mutation_confirm.json", "coupling.md", "revision3_checks.json",
                 "environment.json", "rev3_metrics_behaviour.json",
                 # ревизия 4
                 "revision4_checks.json", "mutation_confirm_rev4.json", "rev4_stepshield.json",
                 "rev4_paraphrase_eval.json", "rev4_repeat_compare.json", "rev4_live_summary.json",
                 # ревизия 6
                 "revision6_checks.json", "rev6_mutations.json", "rev6_foundation_table.json",
                 "rev6_boundary_diag.json", "rev6_evaluation-results.md",
                 "rev6_confirm_mode_compare.json", "rev6_cell_budget.json",
                 "rev6_attack_cards_validation.json",
                 # ревизия 5
                 "revision5_checks.json", "mutation_confirm_rev5.json", "rev5_confirm_probe.json",
                 "rev5_blind_eval.json", "rev5_confirm_impact.json", "rev5_window_table.json",
                 "rev5_window_table.md", "rev5_front_regression.json", "rev5_live_summary.json",
                 "rev5_starvation.json"):
        if (verify / name).exists():
            shutil.copy2(verify / name, DEST / name)
    shutil.copytree(verify / "logs", DEST / "logs")
    for res in sorted(list((verify / "rev4_live_replay").glob("*/results.json"))
                      + list((verify / "rev5_live_replay").glob("*/results.json"))):  # ревизии 4–5
        dst = DEST / res.parent.parent.name / res.parent.name
        dst.mkdir(parents=True, exist_ok=True)
        for f in ("results.json", "meter.json"):
            if (res.parent / f).exists():
                shutil.copy2(res.parent / f, dst / f)
    if (verify / "rev6_relabel").is_dir():   # ревизия 6: пересчёт живых строк
        shutil.copytree(verify / "rev6_relabel", DEST / "rev6_relabel")
    mech = verify / "rev3_mechanism"
    if mech.is_dir():   # ревизия 3: оценка механизма claims (без журналов событий)
        (DEST / "rev3_mechanism").mkdir(parents=True)
        for f in ("rev3_mechanism.json", "rev3_mechanism.md"):
            if (mech / f).exists():
                shutil.copy2(mech / f, DEST / "rev3_mechanism" / f)
        for sd in sorted(mech.glob("*__*/seed-*/metrics")):
            shutil.copytree(sd, DEST / "rev3_mechanism" / sd.relative_to(mech))
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
        "приёмка ключевых требований, проверки ревизий 1–6 (revision6_checks.json, rev6_*.json, rev6_relabel/, revision2_checks.json, revision3_checks.json, revision4_checks.json, revision5_checks.json, mutation_confirm_rev5.json, rev5_*.json, rev5_live_replay/, mutation_confirm_rev4.json, rev4_stepshield.json, rev4_paraphrase_eval.json, rev4_repeat_compare.json, rev4_live_replay/, coupling.json, mutation_confirm.json, environment.json, rev3_mechanism/), журналы шагов, сводки и метрики сквозного "
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
    ap.add_argument("--dest", default="revision6_verify",
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
