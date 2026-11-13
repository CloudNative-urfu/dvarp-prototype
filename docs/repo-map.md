# Карта репозитория

Навигация по `dvarp-prototype` для ИИ-агента и новых разработчиков: что где лежит и в каком
порядке читать. Это не инструкция запуска: система не гарантированно работает end-to-end
(см. раздел 7). Правила работы — `AGENTS.md`, обзор — `README.md`.

У каждого пакета `darp_range/<модуль>/` подробный docstring в `__init__.py` (состав
подмодулей, требования FR/NFR, CLI). Ниже — только ориентиры; детали читать там.

## 1. Ядро измерений

Путь данных одного прогона:

```
report_cli ──► runner ──► channel ◄──► agent + tools ──► executor ──► state_store
 (CLI)        (план,      (текст,      (LLM-агент,       (проверка    (SQLite,
              снапшоты,    барьер)      5 инструментов)   прав)        снимки)
              пары)                                                       │
                                                                          ▼
 report_cli ◄── metrics ◄──────────────────────────────────────────── evaluator
 (таблица)     (L*, ΔNet)                                            (исход по БД)
```

| Модуль | Что делает | Тест | Docstring |
| --- | --- | --- | --- |
| `runner` | Оркестрация: `RunPlan` → эпизоды и потоки; восстановление снимка с проверкой хеша, парный прогон «без атак / с атаками» (`run_paired`), повтор для поиска расхождений, `Runner` по всем конфигурациям с `FreezeGuard`. | `tests/test_runner.py` | `darp_range/runner/__init__.py`, `flow.py`, `orchestrator.py` |
| `channel` | Единственный текстовый канал между сторонами (`TextChannel.run_session`), барьер интерфейса: атакующий видит только `Observation`, защита — только текст. Отдельно маркированный research-mode. | `tests/test_channel.py` | `darp_range/channel/__init__.py` |
| `agent` + `tools` | `RefundAgent.respond(text)`: цикл «модель → инструменты → ответ», о гвардрейлах не знает (FR-1.9). Ровно 5 инструментов: `read_order`, `read_ticket`, `get_refund_policy`, `ask_customer`, `finalize_case` (assert в `tools/specs.py`). `Toolbox.call` — разбор → перехват конвейером (`ToolInterceptor`) → исполнитель. | `tests/test_agent.py`, `tests/test_tools.py` | `darp_range/agent/__init__.py`, `darp_range/tools/__init__.py` |
| `executor` | Граница безопасности `g0`: роли, права, принадлежность заказа, разовые операции, транзакции с откатом. Декларации границы `boundary_spec` (v1), `boundary_spec_v2`, `boundary_spec_v2r` в `boundary.py`; основа выбирается флагом `--foundation v1|v2|v2r`. | `tests/test_executor.py`; v2r — `tests/test_rev10_v2r.py`, `tests/test_review_routing_v2r.py` | `darp_range/executor/__init__.py`, `boundary.py` |
| `state_store` | Синтетическая БД магазина (SQLite), детерминированный генератор, снимок `take_snapshot` и полное восстановление `restore_snapshot`, `diff_states`. | `tests/test_state_store.py` | `darp_range/state_store/__init__.py`, `store.py` |
| `evaluator` | Независимый оценщик: сверка итогового состояния БД с ground truth без чтения текста агента (FR-1.18), исходы атаки `executed`/`refused`/`ignored` (FR-1.19), `finalize_pair` (slow/burn); эскалация — нейтральный исход, не успех атаки (цель `shift` исключена, FR-1.21). | `tests/test_evaluator.py` | `darp_range/evaluator/__init__.py` |
| `metrics` | Метрики безопасности, полезности, непрерывности, детекции и экономики (`extract.py`): доля эскалаций по классам трафика `E_L`/`E_A` и аналитический блок очереди λ_Q → W_q/Q_L → SLA; ΔNet и порог L* с членами `C_queue`/`C_sla` и статусом `queue_unstable` (`breakeven.py`), Pareto и leave-one-out (`frontier.py`), сборка отчёта (`analysis.py`). | `tests/test_metrics.py` | `darp_range/metrics/__init__.py`, `breakeven.py` |
| `report_cli` | CLI `offline` / `live` / `verify`: runner → metrics → L* → индекс артефактов NFR-2.14 → сводная таблица; аудит контура (запрет сети, только stdlib). | `tests/test_report_cli.py` | `darp_range/report_cli/__init__.py`, `__main__.py` (все флаги) |

Вспомогательные модули, через которые идёт этот путь:

| Модуль | Роль | Тест |
| --- | --- | --- |
| `event_log` | Единый журнал событий и затрат с раздельным учётом сторон (FR-1.17, NFR-2.9), хеш-цепочка. | `tests/test_event_log.py` |
| `manual_queue` | Аналитическая очередь операторов (ревизия канона dvarp#18): `analytic.py` — M/M/1 при K=1, M/M/c (Эрланг C) при K>1 от `E_L`/`E_A`, SLA-вероятность, сетка K×S, `self_check`; `arrivals.py` — расписание прибытий потока; `model.py` (DES) — только проверка формулы, в измерительный путь не входит. | `tests/test_manual_queue.py` |
| `llm` | Клиент OpenAI-совместимой модели (`RESEARCH_LLM_*`) и детерминированная заглушка, лимиты токенов. | `tests/test_llm.py` |
| `repro` | Seed, манифест прогона, хеши корпуса и конфигураций. | `tests/test_repro.py` |
| `agent_demo` | Мини-эпизод агента с гвардрейлами для demo/live (вынесен, чтобы агент не ссылался на гвардрейлы). | через `tests/test_agent.py` |
| `shop_svc`, `pay_emu`, `svckit` | Контур ревизии 11: сервис магазина, платёжный эмулятор, payout-worker на 127.0.0.1. | `tests/test_rev11_*.py`, `scripts/rev11_verify.py` |

## 2. Атакующий контур

- `darp_range/attackers/` — атакующая сторона за интерфейсом канала. `make_attacker(...,
  mode="D"|"A")`: режим D — корпус, режим A — адаптивный LLM-агент (офлайн — заглушка,
  живой — через `RESEARCH_LLM_*`), плюс базовые линии `template`/`random`. Превышение лимита
  попыток — нарушение протокола. Тест: `tests/test_attackers.py`.
- `darp_range/isolation/` — изоляция атакующего (ревизия 9): процесс под bubblewrap с
  брокером (`sandbox.py`), закрытая схема IPC (`ipc.py`, код процесса — `child/`), журнал
  точных payload запросов атакующей модели (`audit.py`), матрица доступа
  (`matrix.py` + `configs/isolation/access_matrix.json`), происхождение заданий и
  `claim_level` прогона (`provenance.py` + `configs/isolation/provenance_registry.json`).
  Тесты: `tests/test_isolation_sandbox.py`, `_audit`, `_matrix`, `_probes`, `_provenance`.
- `darp_range/corpus/` + `corpus/` — размеченные атаки по трём осям FR-1.21 (механизм, цель
  `break`/`slow`/`burn`, доставка) и сопоставимые чистые контроли FR-1.22 с ground truth.
  `corpus/darp_corpus_v2.jsonl` (20 атак + 20 контролей; корпус v2 без цели `shift`, #29),
  срезы `holdout_v2/`, `heldout_v3/`, `trusted_v7/`, хеши — `corpus/MANIFEST.json`. Вручную не редактируется: источник — `darp_range/corpus/entries.py`,
  проверка — `python3 -m darp_range.corpus verify`. Тест: `tests/test_corpus.py`.
- `artifacts/attack-cards.json` — паспорта атак и контролей (версия `attack-cards-2 (rev7)`):
  цель, точка входа, граница доверия, нарушаемый инвариант, оракул, критерий успеха, статус
  включения. Генерация — `scripts/make_attack_cards.py --out artifacts/attack-cards.json
  --live-from <прежний файл>` (живые трассы режима A в Git не входят), проверка —
  `scripts/validate_attack_cards.py`.

## 3. Гвардрейлы

- Контракт плагина — `darp_range/guard_contract/` (docstring в `__init__.py`, типы —
  `types.py`, база — `plugin.py`). Вход `GuardInput`: текст, `ContextFragment` с
  происхождением, `TrustedTask`, неисполненный `ProposedCall`. Выход `GuardSignal`:
  `allow` / `flag` / `block` + score. `GuardRuntime` открывает БД только на чтение и
  начисляет затраты; `GuardHost` пишет `guard_signal`/`guard_error` в журнал и ловит запись
  в БД (пассивность). Тест: `tests/test_guard_contract.py`.
- Конвейер и политика — `darp_range/guard_policy/`: точки `input` / `context` / `tool` /
  `output` (`STAGES` в `guard_contract/types.py`), таблица «точка × сигнал → действие»
  `continue` / `isolate` / `clarify` / `escalate` / `terminate` (`policy.py`), исполнение
  реакции кодом через исполнитель (`pipeline.py`), обёртка агента `GuardedDefender`
  (`defender.py`). Тест: `tests/test_guard_policy.py`.
- Плагины — `darp_range/guard_plugins/`: встроенные офлайн `input-filter`,
  `tool-arg-validator`, `context-provenance`, `output-leak` (`builtin.py`, правила —
  `rules.py`, нормализация текста — `textnorm.py`); измеренный OSS-адаптер DeBERTa
  `deberta-pi-v2` (`oss_deberta.py`, офлайн — по кэшу оценок `corpus/oss_scores/`);
  выключенные по умолчанию адаптеры `qwen3guard-gen-4b`, `llama-guard-3-8b`,
  `granite-guardian-3.1-2b`, `presidio-pii` (`adapters.py`). Тест:
  `tests/test_guard_plugins.py`.

## 4. Конфигурации

- `configs/*.json` — данные, а не код: набор гвардрейлов и точки, политика реакции, правила
  процесса, защищаемая сторона. Набор — исследовательская матрица §4 (#29): `g0`, `g_proc`,
  позиционная матрица × политики `canon_isolate`/`canon_escalate`/`canon_terminate`, `g_in_oss`,
  контроли, leave-one-out; подробная карта — `docs/configs.md`. Схема и проверки полей — `darp_range/configs/spec.py` (`ConfigSpec`),
  предзарегистрированный набор — `registry.py` (`preregistered`).
- `configs/configs.lock.json` — заморозка (NFR-2.10): sha256 каждого файла, отпечаток набора,
  хеши каталогов кода защиты. Прежние lock — `configs/history/`.
- `darp_range/configs/freeze.py`: `verify` (дрейф файлов и кода), `load_frozen` (загрузка набора,
  любой дрейф — ошибка), `FreezeGuard` (сверка собранной защиты перед каждым эпизодом).
- Что будет при правке: изменённый JSON или код защиты → `verify` сообщает дрейф,
  `load_frozen` и `FreezeGuard` бросают `FrozenConfigError`, прогон не стартует.
  Проверка: `python3 -m darp_range.configs verify --dir configs`.

## 5. Что заморожено и почему

Хешируются (`DEFENSE_CODE` в `darp_range/configs/freeze.py`, раздел `defense_code` lock) и НЕ
редактируются без перезаморозки, согласованной с владельцем:

```
darp_range/configs/
darp_range/guard_contract/
darp_range/guard_plugins/
darp_range/guard_policy/
darp_range/executor/
darp_range/tools/
```

Причина: решение о защите должно воспроизводиться из (данные, конфигурация, код). Если код
защиты меняется после заморозки, прежние прогоны и L* перестают быть сопоставимыми. Также не
трогать `configs/*.json`, `configs/configs.lock.json` и архив `revisions/`.

## 6. Стек и зависимости

- Ядро каркаса — только стандартная библиотека Python ≥ 3.11 (`pyproject.toml`:
  `dependencies = []`; офлайн-прогон аудирует импорты, `report_cli/contour.py`).
- Optional — OSS-детекторы (onnxruntime / tokenizers / numpy) в отдельных venv: см.
  `darp_range/guard_plugins/oss_deberta.py`, `configs/oss/`, `scripts/oss_fetch.py`.
- Направление проекта — сервисная декомпозиция (защитник и атакующий как сервисы,
  скриптовый оркестратор); там появятся свои зависимости.

## 7. Статус каркаса

Это фабричный каркас (снимок ревизии 11 автономной фабрики), выбранный командой для
допилки. Полная верификация из чистой копии отложена с обоснованием — см.
`openspec/changes/repo-cleanup/tasks.md` (раздел Notes) и issue #2.

> `Runner`/`report_cli offline` — legacy in-process путь; решение оставить/выпилить отложено
> до пилота скриптового оркестратора.

## 8. Гарантированно рабочие команды

```sh
ruff check .                                   # линтер, секунды; блокирует PR в CI
python3 -m unittest discover -s tests -t .     # полный набор, ~15–30 мин
python3 -m unittest discover -s tests -t . -p test_executor.py   # один модуль
python3 -m darp_range.configs verify --dir configs               # заморозка конфигураций
```

- `python3 -m darp_range.report_cli offline ...` на текущем каркасе полным прогоном не
  верифицирован (issue #2).
- Часть тестов требует bubblewrap (`nix shell nixpkgs#bubblewrap`): без него они
  пропускаются с явной причиной (`tests/isolation_util.py`, `bwrap_works`) или честно падают
  (fail-closed), а не проходят молча.
