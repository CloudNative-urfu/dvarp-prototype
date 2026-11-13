# Карта замороженных конфигураций

Конфигурации гвардрейлов — данные, а не код: L* считается по прогонам каждой конфигурации
относительно базовой линии `g0`. Здесь — что лежит в `configs/`, чем конфигурации отличаются
и как они попадают в прогон. Источник истины — сами файлы `configs/*.json` и код
`darp_range/configs/`; при расхождении верить им.

В `configs/` сейчас **24** конфигурации — исследовательская матрица dvarp
`guardrails-research-design.md` §4 после перезаморозки issue #29 (вобрала #7): базовые линии
`g0` + `g_proc`, позиционная матрица гвардрейлов × три политики реакции канона, OSS-детектор на
входе, обязательные контроли и leave-one-out. Структурированная форма `g_simple` исключена
каноном (отклонение FR-1.12, dvarp#17). Список ниже совпадает с разделом `configs` в
`configs/configs.lock.json`; набор собирается функцией `preregistered()` в
`darp_range/configs/registry.py`.

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
| `family` | `guarded` | семейство: `g0` / `g_proc` / `guarded` (`FAMILIES`) |
| `role` | `main` | роль в наборе: `baseline` / `main` / `control` / `loo` / `post_hoc` (`ROLES`) |
| `defender` | `agent` | защищаемая сторона: только LLM-агент (`simple` исключён вместе с `g_simple`) |
| `guards` | `[{"id": "input-filter", "stages": ["input"], "params": {}}]` | какие плагины и в каких точках стоят (`input` / `context` / `tool` / `output`); пустые `params` — умолчания плагина |
| `policy.stages.<точка>.on` | `allow→continue, flag→escalate, block→terminate, error→continue` | реакция на сигнал и на сбой плагина: `continue` / `isolate` / `clarify` / `escalate` / `terminate` (`darp_range/guard_policy/policy.py`, `StageRule`) |
| `policy.stages.<точка>.score_flag`, `score_block` | `null` | пороги политики для сигналов с оценкой (оценка ≥ порога → `flag`/`block`); во всех 24 конфигурациях `null`, пороги живут в параметрах плагинов (4-е измерение §4 — умолчания плагинов) |
| `policy.detect_only` | `false` | режим «чистый детектор»: сигналы пишутся, исполняется всегда `continue` |
| `policy.max_clarifications` | `2` | лимит уточнений на эпизод |
| `policy.name`, `policy.version` | `g_in`, `guard-policy-1` | имя таблицы (= имя конфигурации; правила — из предустановки канона `canon_escalate`) и версия политики |
| `process` | `null` | правила процесса (только у `g_proc`, `ProcessSpec` в `darp_range/configs/process.py`) |
| `queue_discipline` | `fifo` | декларация дисциплины общей очереди: `fifo` или `fair_share` (`g_proc`); очередь аналитическая (dvarp#18), средняя задержка от дисциплины не зависит |
| `seed_label` | `main` | метка seed: `main` — общий master seed прогона; иная метка — производный seed (`config_seed` в `darp_range/configs/build.py`). Отдельного поля `seed` в конфигурации нет: seed задаётся прогоном (`--seeds`) |
| `control_of` | `null` | для контролей и leave-one-out — имя конфигурации, которую они контролируют |
| `notes` | текст | короткое описание |

Вычисляемые поля:

| Поле | Смысл |
| --- | --- |
| `executor_boundary` | отпечаток границы исполнителя; один для всех конфигураций (FR-1.14), иное значение — ошибка загрузки |
| `resolved` | параметры плагинов с умолчаниями и их метаданные (у `input-filter`: `flag_at 0.5`, `block_at 0.85`, `quote_discount 0.4`, `min_b64_len 16`), отпечатки набора гвардрейлов, политики и процесса |
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

### Три политики реакции канона

Канон фиксирует три политики реакции (design.md §10), не называя их; принятая трактовка (issue
#29, ждёт подтверждения владельца): политики различаются только реакцией на `flag`
(неуверенный сигнал), по одному семейству действий канона сверх «продолжить»; `block` во всех
трёх — безопасное завершение; сбой плагина — fail-open (`continue`), на инструменте
fail-closed (`isolate`). «Продолжить» — это контроль «чистый детектор». Предустановки —
`PRESETS` в `darp_range/guard_policy/policy.py`.

| Политика | Суффикс имени | `flag` | `block` | `error` |
| --- | --- | --- | --- | --- |
| `canon_isolate` (изолировать фрагмент) | `_iso` | `isolate` | `terminate` | `continue`, на tool `isolate` |
| `canon_escalate` (ручная проверка) | — | `escalate` | `terminate` | `continue`, на tool `isolate` |
| `canon_terminate` (безопасное завершение) | `_term` | `terminate` | `terminate` | `continue`, на tool `isolate` |

### Позиционная матрица

Встроенный гвардрейл в каждой точке установки и все четыре вместе × три политики (15
конфигураций, семейство `guarded`, роль `main`). Пороги — умолчания плагинов (раздел 1).

| Набор (точка) | `canon_isolate` | `canon_escalate` | `canon_terminate` |
| --- | --- | --- | --- |
| `input-filter` @ input | `g_in_iso` | `g_in` | `g_in_term` |
| `context-provenance` @ context | `g_ctx_iso` | `g_ctx` | `g_ctx_term` |
| `tool-arg-validator` @ tool | `g_tool_iso` | `g_tool` | `g_tool_term` |
| `output-leak` @ output | `g_out_iso` | `g_out` | `g_out_term` |
| все четыре (`g_layered`) | `g_layered_iso` | `g_layered` | `g_layered_term` |

Точка «среда» (§4) в каркасе плагина не имеет — в матрицу не входит.

### Базовые линии, детектор, контроли и leave-one-out

| Имя | Семейство / роль | Назначение | Отличие от `g0` | Где встречается |
| --- | --- | --- | --- | --- |
| `g0` | g0 / baseline | агент + исполнитель без гвардрейлов, точка отсчёта L* | — | обязательна в каждом прогоне (`REFERENCE` в `report_cli/pipeline.py`); `STRESS_CONFIGS` в `scripts/verify.py`; почти все тесты |
| `g0_seed2` | g0 / control (`control_of g0`) | повтор `g0` на другом seed: разброс seed | то же, `seed_label repeat-2` | `test_configs`, `test_runner`, `test_metrics`, `test_report_cli`, `test_revision_acceptance`, `test_revision_unresolved` |
| `g_proc` | g_proc / baseline | дешёвые правила процесса вместо гвардрейлов | `process`: дедупликация (окно 60 мин), квота 3 / 60 мин, `max_rounds 6`, `max_agent_tokens 24000`, проверки завершения R1/R2/R5; `queue_discipline fair_share` | `test_configs`, `test_runner`, `test_report_cli`, `test_acceptance`; `STRESS_CONFIGS`; `scripts/mutate_configs.py` |
| `g_in_oss` | guarded / main | OSS-детектор DeBERTa на месте и с реакцией `g_in` (сравнение детекторов при фиксированной реакции) | `deberta-pi-v2` @ input; `canon_escalate`; офлайн — по кэшу `corpus/oss_scores/` | `test_report_cli` |
| `g_in_detect_only` | guarded / control (`control_of g_in`) | «чистый детектор» к `g_in`: та же таблица, реакция выключена | как `g_in` + `detect_only true` | `test_configs`, `test_runner`, `test_metrics`, `test_report_cli`, `test_acceptance`, `test_rev10_v2r` |
| `g_layered_minus_input_filter` | guarded / loo (`control_of g_layered`) | leave-one-out `g_layered` без `input-filter` | `g_layered` без точки input | demo `metrics`, полный прогон |
| `g_layered_minus_context_provenance` | guarded / loo | leave-one-out без `context-provenance` | `g_layered` без точки context | demo `metrics`, полный прогон |
| `g_layered_minus_tool_arg_validator` | guarded / loo | leave-one-out без `tool-arg-validator` | `g_layered` без точки tool | demo `metrics`, полный прогон |
| `g_layered_minus_output_leak` | guarded / loo | leave-one-out без `output-leak` | `g_layered` без точки output | demo `metrics`, полный прогон |

Пояснения:

- «Полный прогон»: `report_cli offline` без `--configs` берёт все конфигурации из lock
  (`_prepare` в `darp_range/report_cli/pipeline.py`), поэтому все 24 участвуют в шаге
  `e2e_offline` скрипта `scripts/verify.py` (seeds 7,8,9, основа v2r). Нагрузочный шаг
  `e2e_stress_offline` берёт `STRESS_CONFIGS = "g0,g_in,g_proc,g_layered,g_layered_term"`.
- Leave-one-out конфигурации строит `leave_one_out()` в `darp_range/configs/registry.py`.
- Удалены при перезаморозке #29: `g_simple` (FR-1.12), `g_in_failclosed` (вариант ревизии 1),
  `g_in_tool` и два его leave-one-out (комбинация вне матрицы). Прежний набор — в
  `configs/history/` и под тегом `archive/rev11`.

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
  `guard_policy`, `executor`, `tools` (`DEFENSE_CODE` в `freeze.py`);
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

Легальная перезаморозка (только по согласованию с владельцем, `AGENTS.md`; последняя —
перезаморозка под исследовательскую матрицу, issue #29):

1. Изменить набор в `preregistered()` (`darp_range/configs/registry.py`) и/или код защиты.
2. `python3 -m darp_range.configs freeze --dir configs --replace --note "<причина>"`:
   прежний lock переносится в `configs/history/configs.lock.NNN.<locked_at>.json` (история
   только дополняется), старые файлы конфигураций удаляются, пишутся новые и новый lock.
   Без `--replace` повторная заморозка запрещена.
3. `python3 -m darp_range.configs verify --dir configs` → `OK`.
4. Конфигурация, построенная после блокировки, добавляется не перезаморозкой, а
   `add_post_hoc` (роль `post_hoc`, отдельный раздел lock, в основной вывод не входит).

История блокировок: `configs/history/` (8 прежних lock); текущий lock — `locked_at`
2026-11-13, отпечаток набора `95cc4b3a78c988a9…`.
