# DVARP prototype

Рабочий репозиторий прототипа DARP — полигона оценки гвардрейлов внутри
бизнес-процесса агента возвратов интернет-магазина. Исходное состояние —
снимок ревизии 11 прототипа из `darp-prototype-runs`, вынесенный в корень.

Правила работы (issue ↔ доска, ветки, PR, коммиты) — `AGENTS.md`.

## Модель веток

- `develop` — интеграционная ветка (default): сюда вливаются PR из веток задач
  (`feat/<номер-issue>-<slug>` и т.п.) со squash-merge.
- `main` — релизная ветка, защищена; обновляется только PR'ами из `develop`.

- `darp_range/` — код полигона (только стандартная библиотека Python, >= 3.11).
- `tests/`, `scripts/` — офлайн-проверки и служебные скрипты.
- `configs/`, `corpus/`, `case/` — замороженные конфигурации, размеченный
  корпус и методология.
- `openspec/changes/` — каталоги изменений (обязательный процесс, см. ниже).
- `revisions/` — архив сохранённых ревизий 2–11 с независимой критикой
  (не линтуется, не изменяется).
- `docs/` — ТЗ прототипа (`brief.md`), исходный README снимка (`PROTOTYPE.md`),
  отчёт ревизии 11.

Полные снимки, сырые прогоны и журналы кампании хранятся локально в
`~/.local/share/research-campaigns/`; экспорт в Git их не заменяет.
Крупные артефакты, веса моделей и окружения в Git не входят.

## Процесс openspec (обязательный)

Любое содержательное изменение оформляется каталогом
`openspec/changes/<change-id>/` — изменение без спецификации не принимается.
Формат — по образцу ревизий 9–11 (`attacker-isolation-boundary`,
`trusted-trace-and-v2r-table`, `returns-operator-payout-slice`):

- `.openspec.yaml` — `schema: spec-driven`, дата создания;
- `proposal.md` — Why / What Changes / Capabilities / Impact;
- `design.md` — контекст, цели/нецели, решения с обоснованием;
- `tasks.md` — чеклист задач и деталей тестирования;
- `specs/<capability>/spec.md` — требования `SHALL/MUST` со сценариями
  `WHEN/THEN` по каждой capability.

Техническая настройка репозитория (линтер, CI, коммиты) — спецификация не
обязательна, но текущая настройка оформлена как
`openspec/changes/repo-tooling-setup/`.

## Качество кода и CI

- `ruff` — единственный инструмент линтинга и форматирования
  (конфигурация в `pyproject.toml`, минимальный набор правил, `revisions/`
  исключён). `ruff check` блокирует PR в CI.
- Форматирование прогрессивное: `ruff-format` запускается pre-commit хуком
  на изменяемых файлах; вся база разом не переформатируется.
- Полный сквозной verify (~53 мин) и юнит-тесты выполняются локально и не
  входят в CI.

```sh
ruff check .                      # блокирующая проверка
python3 -m unittest discover -s tests -t .
python3 -m darp_range.report_cli offline --out runs/e2e --seeds 7,8,9
python3 scripts/verify.py --out runs/verify       # полная офлайн-верификация, ~25–35 мин
```

### Быстрый smoke

```sh
python3 scripts/dev_smoke.py      # тесты ядра, ~2 мин, время печатается в конце
```

Для итеративной разработки вместо полного `unittest discover` (~15–30 мин): явный список
модулей `test_executor`, `test_channel`, `test_guard_policy`, `test_guard_contract`,
`test_state_store`, `test_evaluator`, `test_metrics`, `test_tools` (без discover, бюджет ≤5
мин). Полный набор перед PR по-прежнему запускается через discover.

`scripts/verify.py` — полная верификация из чистой копии: тесты, заморозка
конфигураций, корпус, пробы компонентов, мутационный набор, сквозной прогон
v2r (seeds 7,8,9) и нагрузочный (seed 7), приёмка ключевых требований,
контур rev11, сжатие сырых файлов. Часть шагов требует bubblewrap (bwrap)
для песочницы атакующего — без них честный отказ (fail-closed).
Одноразовые аналитики закрытых ревизий 1–10 и кампанная обвязка удалены;
исходное состояние — под тегом `archive/rev11` и в `revisions/`.

Замороженный defense-код (`configs`, `guard_contract`, `guard_plugins`,
`guard_policy`, `executor`, `tools`) защищён хешами `configs.lock.json` (NFR-2.10): до
перезаморозки эти файлы не редактируются. Набор конфигураций — исследовательская матрица
dvarp design.md §4 (`docs/configs.md`); очередь — аналитическая от доли эскалаций (dvarp#18).

## Живая модель

Живые пути (`agent_demo live`, `attackers demo --model live`, `report_cli live`) читают модель из
окружения: `RESEARCH_LLM_BASE_URL` (OpenAI-совместимый endpoint локального контура, NFR-2.5),
`RESEARCH_LLM_MODEL`, `RESEARCH_LLM_PARAMS_B` (без него — отказ, NFR-2.6), при необходимости
`RESEARCH_LLM_API_KEY` и `RESEARCH_LLM_EXTRA_BODY` (JSON-объект, добавляется к каждому запросу).

Для локальной llama.cpp с thinking-моделью рассуждения нужно выключить через шаблон чата — иначе
модель тратит бюджет `max_tokens` на рассуждение и возвращает пустой ответ:

```sh
export RESEARCH_LLM_BASE_URL=http://127.0.0.1:8080/v1 RESEARCH_LLM_MODEL=<имя из /models> RESEARCH_LLM_PARAMS_B=<млрд>
export RESEARCH_LLM_EXTRA_BODY='{"chat_template_kwargs":{"enable_thinking":false}}'
python3 -m darp_range.report_cli live --out runs/<name> --attack-mode A --max-requests 20
```

## Веб-консоль

`console/` — локальная консоль на Next.js: запуск всех путей полигона кнопками, живой вывод
процесса, метрики и диалоги прогонов, замороженные конфигурации. Слушает только 127.0.0.1 и
вызывает те же CLI из белого списка. Запуск: `cd console && npm ci && npm run build && npm run start`,
подробности — `console/README.md`.

## Коммиты и задачи

- Conventional Commits (английский), проверка commit-msg через commitizen
  и в CI (по заголовку PR — PR мержится squash'ем).
- Задачи ведутся через issue на доске DVARP Board; реализация — в ветке
  задачи, влив — только через PR в `develop`. Полный workflow — `AGENTS.md`.
- Владелец репозитория разрешает AI-агентам создавать коммиты в ветках
  задач, в том числе автоматически; см. AGENTS.md.
- Секреты, ключи и файлы окружения в Git не включаются.
