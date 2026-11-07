# DARP — zero-версия полигона оценки гвардрейлов

Полигон для сравнения конфигураций гвардрейлов внутри бизнес-процесса агента возвратов
интернет-магазина: две стороны общаются через один текстовый канал, исполнитель проверяет
права, оценщик сверяет состояние БД с ground truth, рассчитываются метрики FR-1.23 и
порог окупаемости `L*` (`case/docs/guardrails-break-even.md`).

ТЗ: `brief.md`, документы в `case/docs/`. Декомпозиция: `artifacts/decomposition.json`.

## Состояние

**Ревизия 5 (2026-11-07, стадия integrate).** Одобренный план `improvement_darp_revision_5/proposal/plan.json`:
позитивная грамматика «yes» легального клиента (`channel/proposal_contract.py`, слепой набор другой сессии —
ложных «yes» 0/38), измерение влияния ошибок клиента на режим L* и фронт (`scripts/rev5_confirm_impact.py`),
допуск по стоимости во фронте и таблица окна вмешательства по всем k (`metrics/`), чередующийся живой режим A
с бюджетом на ячейку (`scripts/live_mode_a_v.py --interleave`). План — `artifacts/improvement-plan.md`,
итоги по ID — `artifacts/improvement-results.md`, предрегистрация — `artifacts/preregistration-rev5.json`,
бюджет — `artifacts/improvement-budget.json`, отчёт — `artifacts/report.md` (раздел R5).

**Ревизия 2 (2026-11-06, стадии integrate и report).** Фактическое состояние, команды запуска, итог
последней проверки, упрощения и остатки — `artifacts/report.md` (разделы R2.0–R2.8). По критике `revision_2_input/critique.md` и
`revision_2_input/case-update.md`: confirm легального клиента сверяет предложение с фактами клиента
(мутации ловятся в verify); OSS-детектор DeBERTa — плагин `oss_deberta` и конфигурация `g_in_oss` в
lock v3 (загрузка `scripts/oss_fetch.py`, ресурсная проверка `scripts/oss_resource_check.py`);
StepShield, τ*, бета-биномиальный гейт, bootstrap L*; приёмка FR-1.16 — по механизму очереди;
coupling-анализ «уязвимость × тревога» (`scripts/coupling_analysis.py`); живой срез
`scripts/run_live_slice.py`. Итоги — `artifacts/revision-2-results.md`, отчёт — `artifacts/report.md`.

**Ревизия 1 (2026-11-04, стадия integrate).** По критике `revision-input/critique.md` и
`revision-input/case-update.md`: отвечающий легальный клиент режима D (`channel/legal_user.py`),
статус `unresolved` при статистически неразрешённом ΔV, verify на seeds 7, 8, 9 с приёмкой
статистической честности, режимы очереди (нагрузка ×3, деградация ревьюера) с ΔQ/L* в основной
таблице, `g_in_failclosed` и инъекция сбоев гвардрейла, holdout v2 (перефразы Qwen, хеш до оценки,
`corpus/holdout_v2/`), флаг контура живой модели по фактическому провайдеру, режим A в живом
прогоне, замер локального OSS-детектора (`scripts/oss_detector_eval.py`, вне verify). Итоги —
`artifacts/revision-results.md`, план — `artifacts/revision-plan.md`, предрегистрация —
`artifacts/preregistration-rev1.json`. Ниже — описание исходной версии (сохранено).

Стадия **report** (последняя проверка: verify PASS из чистой копии, отчёт — `artifacts/report.md`); компоненты собраны в единый полигон со сквозной офлайн-проверкой (`scripts/verify_e2e.py`). Готовы `repro`, `state_store` (синтетическая SQLite-БД, снимки; `python3 -m darp_range.state_store --help`), `event_log` `corpus` (замороженный размеченный корпус, `python3 -m darp_range.corpus verify`), `executor` и `llm` (клиент RESEARCH_LLM_* и детерминированная заглушка; `python3 -m darp_range.llm demo` — офлайн, `python3 -m darp_range.llm probe --max-requests 1` — живой вызов), `guard_contract` (контракт плагина-гвардрейла, реестр, хост с учётом затрат и запретом записи в БД; `python3 -m darp_range.guard_contract demo`), `manual_queue` (общая очередь ручных разборов: дискретно-событийная модель, парные прогоны и сетка чувствительности 4/8/15 мин × 1/2 оператора; `python3 -m darp_range.manual_queue demo`), `channel` (единственный текстовый канал: барьер интерфейса, протокол и лимит реплик, отдельно маркированный research-mode, единый интерфейс режимов D/A; `python3 -m darp_range.channel demo`), `evaluator` (сверка итогового состояния БД с ground truth без текста агента, исходы атаки executed/refused/ignored в журнале, парные цели slow/burn; `python3 -m darp_range.evaluator demo`), `tools` (ровно пять инструментов агента возвратов поверх исполнителя с точкой перехвата для гвардрейлов «инструмент»/«контекст»; `python3 -m darp_range.tools demo`, `python3 -m darp_range.tools specs`), `guard_plugins` (встроенные офлайн-плагины `input-filter`, `tool-arg-validator`, `context-provenance`, `output-leak` и выключенные по умолчанию адаптеры OSS-детекторов Qwen3Guard/Llama Guard 3/Granite Guardian/Presidio; `python3 -m darp_range.guard_plugins catalog`, `python3 -m darp_range.guard_plugins eval` — офлайн-замер детекции на корпусе), `guard_policy` (конвейер точек установки input/context/tool/output и политика реакции continue/isolate/clarify/escalate/terminate с исполнением через роль `policy` исполнителя, режим «чистый детектор», обёртка `GuardedDefender` без изменения агента; `python3 -m darp_range.guard_policy demo`, `python3 -m darp_range.guard_policy presets`), `attackers` (атакующая сторона за интерфейсом канала: режим D — корпус, режим A — LLM-агент с памятью и лимитами через заглушку или RESEARCH_LLM_*, базовые линии template/random; превышение лимитов — нарушение протокола; `python3 -m darp_range.attackers demo`), `agent` (агент возвратов: LLM с пятью инструментами за интерфейсом канала, не знающий о гвардрейлах; детерминированная заглушка с явным профилем уязвимостей; `python3 -m darp_range.agent spec`, офлайн-demo `python3 -m darp_range.agent_demo demo`, живой прогон с жёсткими лимитами `python3 -m darp_range.agent_demo live --entries ctl-01,atk-03`), `simple_baseline` (g_simple: структурированная форма и правила R1–R7 без LLM за тем же интерфейсом канала, через те же пять инструментов и исполнитель; `python3 -m darp_range.simple_baseline spec`, офлайн-demo со сравнением с g0 `python3 -m darp_range.simple_baseline demo`), `configs` (предзарегистрированный и замороженный по хешу набор конфигураций: g0, повтор g0 на другом seed, g_proc — дедупликация, квоты, лимиты, типизированное завершение через роль `process`, g_simple, конфигурации с гвардрейлами, чистый детектор, leave-one-out; граница исполнителя одна для всех; `python3 -m darp_range.configs list|verify --dir configs|demo`; обновление кейса a805eee — `artifacts/case-update.md`), `runner` (оркестратор: восстановление снимка с проверкой хеша перед каждым эпизодом, общий поток с прибытиями для всех конфигураций одного seed, парные потоки без атак и с атаками со сверкой легальных траекторий и влиянием на очередь, повтор потоков с фиксацией дефектов воспроизводимости, раздельный учёт сторон, манифест и файлы результатов; `python3 -m darp_range.runner demo`, `python3 -m darp_range.runner run --out runs/r1 --split dev`), `metrics` (метрики FR-1.23 и break-even по каталогу прогона: V/R/Q/E/C_ep/C_guard, ΔNet, L* с базой, границами и 16 углами цен, статусы случаев ΔV ≤ 0, Pareto-фронт, leave-one-out с пометкой избыточности, список неэффективных, стоимость на предотвращённое нарушение, self-limited отдельной строкой; `python3 -m darp_range.metrics compute --run runs/r1 --out runs/r1/metrics`, `python3 -m darp_range.metrics demo`), `report_cli` (сквозной прогон runner → metrics → таблица метрик и L* → индекс артефактов NFR-2.14 → сводный отчёт; офлайн — под запретом сети, с аудитом контура NFR-2.5 и seed floor; живой — через RESEARCH_LLM_* с общим жёстким бюджетом запросов/токенов/эпизодов/времени на весь прогон; `verify` перепроверяет готовый каталог).
Все 20 компонентов декомпозиции реализованы; сводка и упрощения — `artifacts/`. Ход работ — в `progress.md`.

## Требования к окружению

- CPython ≥ 3.11 (проверено на 3.14.8).
- Внешних зависимостей нет: только стандартная библиотека (`requirements.txt` пуст намеренно).
- Сеть для проверок не нужна. Живая модель используется только в живом прогоне через
  переменные окружения `RESEARCH_LLM_BASE_URL`, `RESEARCH_LLM_MODEL`, `RESEARCH_LLM_API_KEY`.

## Запуск

Все команды выполняются из корня рабочего каталога, установка пакета не требуется.

```sh
# проверка декомпозиции по workflow-contract.json
python3 scripts/validate_decomposition.py

# все офлайн-тесты
python3 -m unittest discover -s tests -t .

# проверка отдельного компонента (check_command из decomposition.json)
python3 -m unittest discover -s tests -t . -p test_<component>.py
```

Сквозной прогон (компонент `report_cli`):

```sh
# офлайн: все 17 замороженных конфигураций, срез dev, seed floor 7,8,9 (≈7 мин; один seed ≈2,5 мин)
python3 -m darp_range.report_cli offline --out runs/e2e --seeds 7,8,9
# повторная проверка готового каталога
python3 -m darp_range.report_cli verify --out runs/e2e
# живой: RESEARCH_LLM_*, по умолчанию g0,g_in × atk-02,ctl-02; ≤60 запросов, ≤16 эпизодов, ≤900 с
python3 -m darp_range.report_cli live --out runs/live1
```

Результаты: `<out>/report.md` (таблица L* с границами цен, Pareto, неэффективные, NFR-2.14),
`<out>/summary.json`, `<out>/seed-<s>/metrics/*.csv`.

## Сквозная проверка (integrate)

`artifacts/submission.json`:

```sh
# verify_command: из чистой копии, офлайн, ~20–25 мин (seeds 7,8,9 + прогоны ревизии 1); код 0 только если всё прошло
python3 scripts/verify_e2e.py --out runs/verify
# run_command: живой прогон через RESEARCH_LLM_* с жёсткими лимитами
python3 -m darp_range.report_cli live --out runs/live --configs g0,g_in --entries atk-02,ctl-02,ctl-04 \
    --max-requests 60 --max-tokens 150000 --max-episodes 16 --max-wall-s 900 --max-requests-per-episode 12
```

`verify_e2e.py`: окружение/зависимости (stdlib) → все unittest → валидатор декомпозиции, хеши
конфигураций и корпуса → demo-пробы компонентов → сквозной `report_cli offline` всех 17
конфигураций (seed 7) + `verify` → нагрузочный прогон очереди + `verify` → приёмка 17 ключевых
требований (`darp_range/report_cli/acceptance.py`) по файлам прогонов. Выход:
`runs/verify/{verify.json,verify.md,key_requirements.json,logs/,e2e/,e2e_stress/}`.
`python3 scripts/make_status.py --verify runs/verify` копирует доказательства в
`artifacts/checks/integrate_verify/` и собирает `artifacts/status.json`.

## Структура

| Путь | Назначение |
| --- | --- |
| `darp_range/<component>/` | код компонента (подпакет на каждый id из декомпозиции) |
| `tests/test_<component>.py` | офлайн-проверка компонента |
| `configs/` | предзарегистрированные замороженные конфигурации (JSON) и `configs.lock.json` |
| `corpus/` | размеченный корпус атак и чистых контролей (JSONL, английский) |
| `scripts/` | служебные скрипты (валидатор декомпозиции, сквозной прогон) |
| `artifacts/` | доказательства: декомпозиция, описания компонентов, упрощения, отчёты прогонов |
| `runs/` | рабочие выходы прогонов (не входят в артефакты; итоговые отчёты копируются в `artifacts/`) |

Язык: внутри полигона (сценарий, корпус, промпты, правила, сообщения) — английский;
документация и комментарии — русский; идентификаторы и API — английские (NFR-2.7).
