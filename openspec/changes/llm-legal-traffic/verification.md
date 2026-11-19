# Verification — 2026-11-19

Base: `develop` `ae27e98` (post-#73/#75); branch: `feat/76-llm-traffic`.
Python 3.14.7, Nix (Linux).

| Check | Result |
| --- | --- |
| `pre-commit` (ruff, ruff-format) по изменённым файлам | passed |
| `python3 -m unittest tests.test_llm_user` (новый модуль, 10 тестов) | OK |
| `python3 scripts/dev_smoke.py` (включая `tests.test_llm_user`) | 291 tests, 12 modules, OK, 88.5 s (budget 300 s) |
| Регрессия runner/CSV-деплоя/легального клиента (`test_runner`, `test_state_store_deploy`, `test_revision_legal_runner`) | 47 tests, OK |
| `tests.test_report_cli` (аудит контура: только stdlib в runtime) | 25 tests, OK |
| `tests.test_channel`, `tests.test_agent` (канал и агент не тронуты) | 57 tests, OK |

Покрытие тестов `tests/test_llm_user.py`:

- `LlmUser`: первая реплика из корпуса без вызова модели; промпт только из фактов
  клиента (order/item/reason/цена/дни/email), история ролей system→user→assistant;
  `end_reason` (closed_by_agent, turn_limit); `summary()` с usage и версией модели;
  детерминизм при одинаковых seed; исчерпание лимита запросов модели — исключение
  (fail-closed).
- `client_config`: отсутствие поля launch = scripted; некорректные kind/mode — ValueError.
- `ensure_model_available`: live без `RESEARCH_LLM_*` — отказ (используется в `State.__init__`,
  до ready.json).
- Traffic-svc E2E (прямой `State.dispatch`): stub-клиент отвечает по диалогу, summary несёт
  usage/версию; scripted-путь побайтно совпадает с эталонным `CorpusTraffic.client` на том же
  эпизоде; live без env — `State()` падает до готовности сервиса.

Полный `scripts/verify.py` на этом окружении не запускался (изменение не затрагивает
замороженные defense-каталоги и формулы; смоук-набор покрывает затронутые инварианты).
Живая проверка LLM-клиента (`--customer llm` + `RESEARCH_LLM_*`) — часть пилота live (#6).
