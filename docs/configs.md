# Карта замороженных конфигураций

Конфигурации гвардрейлов — данные, а не код: L* считается по прогонам каждой конфигурации
относительно базовой линии `g0`. Здесь — что лежит в `configs/`, чем конфигурации отличаются
и как они попадают в прогон. Источник истины — сами файлы `configs/*.json` и код
`darp_range/configs/`; при расхождении верить им.

В `configs/` сейчас **19** конфигураций (в issue #11 указано 17; список ниже взят из
`configs/` и совпадает с разделом `configs` в `configs/configs.lock.json`). Набор собирается
функцией `preregistered()` в `darp_range/configs/registry.py`.

## 1. Анатомия конфигурации на примере `g_in`

Поля делятся на входные (`_INPUT_FIELDS`) и вычисляемые (`_DERIVED_FIELDS`), оба списка — в
`darp_range/configs/spec.py`. Неизвестное поле — ошибка `ConfigError` (в том числе любые
настройки исполнителя, FR-1.14). Вычисляемые поля при загрузке пересчитываются из текущего
кода и сверяются с файлом (`ConfigSpec.from_spec`).

Входные поля (`configs/g_in.json`):

| Поле | Значение в `g_in` | Смысл |
| --- | --- | --- |
| `schema` | `darp-config-1` | версия схемы (`CONFIG_SCHEMA`) |
| `name` | `g_in` | имя, оно же имя файла |
| `family` | `guarded` | семейство: `g0` / `g_proc` / `g_simple` / `guarded` (`FAMILIES`) |
| `role` | `main` | роль в наборе: `baseline` / `main` / `control` / `loo` / `post_hoc` (`ROLES`) |
| `defender` | `agent` | защищаемая сторона: LLM-агент или `simple` (форма без LLM, `g_simple`) |
| `guards` | `[{"id": "input-filter", "stages": ["input"], "params": {}}]` | какие плагины и в каких точках стоят (`input` / `context` / `tool` / `output`); пустые `params` — умолчания плагина |
| `policy.stages.<точка>.on` | `allow→continue, flag→escalate, block→terminate, error→continue` | реакция на сигнал и на сбой плагина: `continue` / `isolate` / `clarify` / `escalate` / `terminate` (`darp_range/guard_policy/policy.py`, `StageRule`) |
| `policy.stages.<точка>.score_flag`, `score_block` | `null` | пороги политики для сигналов с оценкой (оценка ≥ порога → `flag`/`block`); во всех 19 конфигурациях `null`, пороги живут в параметрах плагинов |
| `policy.detect_only` | `false` | режим «чистый детектор»: сигналы пишутся, исполняется всегда `continue` |
| `policy.max_clarifications` | `2` | лимит уточнений на эпизод |
| `policy.name`, `policy.version` | `g_in`, `guard-policy-1` | имя таблицы и версия политики |
| `process` | `null` | правила процесса (только у `g_proc`, `ProcessSpec` в `darp_range/configs/process.py`) |
| `queue_discipline` | `fifo` | дисциплина общей очереди операторов: `fifo` или `fair_share` |
| `seed_label` | `main` | метка seed: `main` — общий master seed прогона; иная метка — производный seed (`config_seed` в `darp_range/configs/build.py`). Отдельного поля `seed` в конфигурации нет: seed задаётся прогоном (`--seeds`) |
| `control_of` | `null` | для контролей и leave-one-out — имя конфигурации, которую они контролируют |
| `notes` | текст | короткое описание |

Вычисляемые поля:

| Поле | Смысл |
| --- | --- |
| `executor_boundary` | отпечаток границы исполнителя; один для всех конфигураций (FR-1.14), иное значение — ошибка загрузки |
| `resolved` | параметры плагинов с умолчаниями и их метаданные (у `input-filter`: `flag_at 0.5`, `block_at 0.85`, `quote_discount 0.4`, `min_b64_len 16`), отпечатки набора гвардрейлов, политики, процесса и `g_simple` |
| `operational_definition` | та же политика человекочитаемыми строками |
| `fingerprint` | отпечаток конфигурации; его сверяют lock и `FreezeGuard` |

Параметры плагинов по умолчанию (из `resolved` всех конфигураций):

| Плагин | Механизм | Параметры |
| --- | --- | --- |
| `input-filter` | rules, builtin | `flag_at 0.5`, `block_at 0.85`, `quote_discount 0.4`, `min_b64_len 16` |
| `context-provenance` | rules, builtin | `flag_at 0.5`, `block_at 0.85`, `quote_discount 0.4`, `taint_score 0.6`, `unknown_rule_weight 0.6` |
| `tool-arg-validator` | rules, builtin | `flag_score 0.6`, `check_refusals true` |
| `output-leak` | rules, builtin | `flag_score 0.6`, `check_internal true` |
| `deberta-pi-v2` | classifier, self_hosted (rev `90c9989`) | `threshold 0.5`, `flag_signal flag` |

## 2. Все конфигурации

Реакции записаны как `flag/block/error` (на `allow` везде `continue`). Во всех конфигурациях
`max_clarifications = 2`, пороги политики `null`.

| Имя | Семейство / роль | Назначение | Отличие от `g0` | Где встречается |
| --- | --- | --- | --- | --- |
| `g0` | g0 / baseline | агент + исполнитель без гвардрейлов, точка отсчёта L* | — | обязательна в каждом прогоне (`REFERENCE` в `report_cli/pipeline.py`); `STRESS_CONFIGS` в `scripts/verify.py`; почти все тесты (`test_configs`, `test_runner`, `test_metrics`, `test_report_cli`, …) |
| `g0_seed2` | g0 / control (`control_of g0`) | повтор `g0` на другом seed: разброс seed | то же, `seed_label repeat-2` | `test_configs`, `test_runner`, `test_metrics`, `test_report_cli`, `test_revision_acceptance`, `test_revision_unresolved` |
| `g_proc` | g_proc / baseline | дешёвые правила процесса вместо гвардрейлов | `process`: дедупликация (окно 60 мин), квота 3 / 60 мин, `max_rounds 6`, `max_agent_tokens 24000`, проверки завершения R1/R2/R5; очередь `fair_share` | `test_configs`, `test_runner`, `test_report_cli`; `STRESS_CONFIGS`; `scripts/mutate_configs.py` |
| `g_simple` | g_simple / baseline | форма + правила возврата без LLM-агента (FR-1.12) | `defender simple` | `test_simple_baseline`, `test_configs`, `test_runner`, `test_metrics`, `test_report_cli`, …; `STRESS_CONFIGS`; `scripts/mutate_simple_baseline.py` |
| `g_in` | guarded / main | фильтр входа | `input-filter` @ input; `escalate/terminate/continue` | `test_configs`, `test_runner`, `test_metrics`, `test_report_cli`, `test_attackers`, `test_rev10_v2r`, …; `STRESS_CONFIGS`; `scripts/live_mode_a_v.py`, `scripts/isolation_verify.py` |
| `g_in_detect_only` | guarded / control (`control_of g_in`) | «чистый детектор» к `g_in`: та же таблица, реакция выключена | как `g_in` + `detect_only true` | `test_configs`, `test_runner`, `test_metrics`, `test_report_cli`, `test_acceptance`, `test_rev10_v2r` |
| `g_in_failclosed` | guarded / main | `g_in` с fail-closed на сбой детектора (ревизия 1) | `input-filter` @ input; `escalate/terminate/terminate` | `test_revision5_metrics` |
| `g_in_oss` | guarded / main | OSS-детектор DeBERTa на месте и с реакцией `g_in` (ревизия 2) | `deberta-pi-v2` @ input; `escalate/terminate/continue`; офлайн — по кэшу `corpus/oss_scores/` | `test_report_cli` |
| `g_tool` | guarded / main | валидатор аргументов инструментов | `tool-arg-validator` @ tool; `isolate/clarify/isolate` | `test_configs` |
| `g_ctx` | guarded / main | происхождение контекста: изолировать недоверенные фрагменты | `context-provenance` @ context `isolate/isolate/continue` и @ tool `continue/isolate/isolate` | `test_revision_acceptance`, `test_revision_unresolved` |
| `g_out` | guarded / main | проверка утечки в ответе | `output-leak` @ output; `isolate/terminate/continue` | `test_metrics`, `test_revision_unresolved` |
| `g_in_tool` | guarded / main | комбинация `g_in` + `g_tool` | `input-filter` @ input `escalate/terminate/continue` + `tool-arg-validator` @ tool `isolate/clarify/isolate` | `test_guard_contract` |
| `g_in_tool_minus_input_filter` | guarded / loo (`control_of g_in_tool`) | leave-one-out `g_in_tool` без `input-filter` | только `tool-arg-validator` @ tool | только полный прогон (все конфигурации) |
| `g_in_tool_minus_tool_arg_validator` | guarded / loo (`control_of g_in_tool`) | leave-one-out `g_in_tool` без `tool-arg-validator` | только `input-filter` @ input | только полный прогон |
| `g_layered` | guarded / main | все встроенные гварды во всех четырёх точках | `input-filter` @ input `escalate/terminate/continue`, `context-provenance` @ context `isolate/isolate/continue`, `tool-arg-validator` @ tool `isolate/clarify/isolate`, `output-leak` @ output `isolate/terminate/continue` | `test_configs`, `test_acceptance`, `test_revision2_metrics`; `STRESS_CONFIGS` |
| `g_layered_minus_input_filter` | guarded / loo (`control_of g_layered`) | leave-one-out `g_layered` без `input-filter` | `g_layered` без точки input | только полный прогон |
| `g_layered_minus_context_provenance` | guarded / loo (`control_of g_layered`) | leave-one-out без `context-provenance` | `g_layered` без точки context | только полный прогон |
| `g_layered_minus_tool_arg_validator` | guarded / loo (`control_of g_layered`) | leave-one-out без `tool-arg-validator` | `g_layered` без точки tool | только полный прогон |
| `g_layered_minus_output_leak` | guarded / loo (`control_of g_layered`) | leave-one-out без `output-leak` | `g_layered` без точки output | только полный прогон |

Пояснения к колонке «Где встречается»:

- «Полный прогон»: `report_cli offline` без `--configs` берёт все конфигурации из lock
  (`_prepare` в `darp_range/report_cli/pipeline.py`), поэтому все 19 участвуют в шаге
  `e2e_offline` скрипта `scripts/verify.py` (seeds 7,8,9, основа v2r). Нагрузочный шаг
  `e2e_stress_offline` берёт только `STRESS_CONFIGS = "g0,g_in,g_proc,g_layered,g_simple"`.
- Leave-one-out конфигурации строит `leave_one_out()` в `darp_range/configs/registry.py`; по
  имени в тестах они не упоминаются.
- Тесты найдены поиском имени конфигурации по `tests/*.py`; список для `g0`, `g_in` и
  `g_simple` сокращён.

## 3. Как конфигурация попадает в прогон

1. `load_frozen("configs")` (`darp_range/configs/freeze.py`) вызывает `verify`: любой дрейф
   файлов или кода защиты → `FrozenConfigError`. Затем каждый JSON читается через
   `ConfigSpec.from_spec`, отпечаток набора сверяется с lock. Так загружает конфигурации и
   `report_cli` (`_prepare`), и `Runner` (`darp_range/runner/orchestrator.py`).
2. `FreezeGuard.from_lock("configs")` создаётся до первого эпизода. Перед каждым эпизодом
   `FreezeGuard.check(cfg, bundle)` сверяет отпечаток конфигурации и реально собранную защиту
   (набор гвардрейлов, политику, процесс) с замороженными, а также что защита не меняется
   между эпизодами.
3. Защищаемая сторона собирается из конфигурации функцией `assemble_defender`
   (`darp_range/configs/build.py`); код агента не меняется (FR-1.9).

Каталог `darp_range/configs/` заморожен: читать можно, править нельзя.

Сменить конфигурацию прогона — флагом, без правки кода:

```sh
python3 -m darp_range.report_cli offline --out runs/cmp --configs g0,g_in
```

`g0` в списке обязателен (иначе L* не определён, `PipelineError`), неизвестное имя — ошибка до
запуска.

## 4. Заморозка

`configs/configs.lock.json` (схема `darp-configs-lock-1`) хранит:

- `configs` — для каждой конфигурации sha256 файла, `fingerprint`, семейство, роль;
- `set_fingerprint` — отпечаток набора;
- `defense_code` — хеши каталогов `configs`, `guard_contract`, `guard_plugins`,
  `guard_policy`, `simple_baseline`, `executor`, `tools` (`DEFENSE_CODE` в `freeze.py`);
- `post_hoc` — конфигурации, добавленные после блокировки (сейчас пусто), `locked_at`, `note`,
  `lock_fingerprint`.

Проверка:

```sh
python3 -m darp_range.configs verify --dir configs   # OK или DRIFT ..., код выхода 1
```

Что ловит `verify` (`Drift.kind`): `file_changed` (sha256 файла), `file_missing`, `unlisted`
(JSON без записи в lock), `code_mismatch` (вычисляемые поля или отпечаток разошлись с кодом,
например сменились умолчания плагина), `defense_code` (изменился код защиты), `lock`
(нарушены роли `post_hoc`). Этот же шаг стоит в `scripts/verify.py` (`configs_frozen`).

Что будет при правке файла конфигурации или кода защиты: `verify` выдаст дрейф,
`load_frozen` и `FreezeGuard` бросят `FrozenConfigError`, `report_cli offline` и `Runner` не
стартуют. Обходной флаг `allow_code_drift=True` в `load_frozen`/`Runner` — только для
разработки, прогон с ним надо явно помечать.

Легальная перезаморозка (только по согласованию с владельцем, `AGENTS.md`; перезаморозка
набора под исследовательскую матрицу — отдельная задача #7):

1. Изменить набор в `preregistered()` (`darp_range/configs/registry.py`) и/или код защиты.
2. `python3 -m darp_range.configs freeze --dir configs --replace --note "<причина>"`:
   прежний lock переносится в `configs/history/configs.lock.NNN.<locked_at>.json` (история
   только дополняется), старые файлы конфигураций удаляются, пишутся новые и новый lock.
   Без `--replace` повторная заморозка запрещена.
3. `python3 -m darp_range.configs verify --dir configs` → `OK`.
4. Конфигурация, построенная после блокировки, добавляется не перезаморозкой, а
   `add_post_hoc` (роль `post_hoc`, отдельный раздел lock, в основной вывод не входит).

История блокировок: `configs/history/` (7 прежних lock, последний `locked_at` текущего lock —
2026-11-07).
