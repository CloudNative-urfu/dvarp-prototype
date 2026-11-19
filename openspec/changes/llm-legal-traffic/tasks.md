# Tasks — issue #76

- [x] Add `channel/llm_user.py`: `LlmUser` (UserEndpoint-протокол), промпт по фактам,
  summary с usage без текста.
- [x] Добавить профиль заглушки клиента (data-only) и фабрику клиента в traffic-svc:
  launch `client.kind`/`mode`, fail-closed без `RESEARCH_LLM_*` для live.
- [x] `RunPlan.customer_mode` (валидация, вне отпечатка при default) + `runner svc --customer`.
- [x] Тесты: LlmUser со stub-моделью (старт из корпуса, история, end_reason, usage),
  детерминизм; traffic-svc E2E со stub-клиентом; отказ без env; scripted-путь без изменений.
- [x] Отчёт: `scripts/dev_smoke.py` зелёный; полный список проверок в verification.md.
- [x] Мануал `docs/manuals/traffic-svc.md` — раздел про клиента; индекс мануалов.
