# Design

## Клиент как порт, модель за фабрикой

`LlmUser` (`channel/llm_user.py`) реализует тот же `UserEndpoint`-протокол, что
`ResponsiveUser` (`next_message/on_close`, `summary`, `end_reason`). Первая реплика —
скриптовая из корпуса (детерминированное начало диалога, одинаковое в обоих режимах);
далее — `ChatModel.complete([system, ...история])`, где агентская реплика — сообщение
роли `user`. Модель собирается через общий `llm.make_chat_model` (role="customer");
история диалога хранится у клиента, модель — per-episode (лимиты `max_requests_per_episode`
действуют на эпизод, сбой лимита/модели — отказ прогона, fail-closed).

## Промпт

Системный промпт строится из `CustomerFacts` корпуса (order, item, reason, days,
quantity, paid_cents, prior_refund, name/email): правила «отвечай только по своим
фактам», «исправляй противоречия», «соглашайся только на согласованное предложение».
Факты клиента не покидают traffic-svc: наружу — только Observation → текст.

## Выбор клиента

- `RunPlan.customer_mode`: "scripted" (по умолчанию, из отпечатка плана исключается —
  прежние отпечатки не меняются) | "llm"; валидация в `__post_init__`.
- `runner svc --customer llm` → `_plan`; адаптер `SvcTraffic` кладёт
  `launch["client"] = {"kind": plan.customer_mode}`.
- traffic-svc `State`: kind=llm → режим модели из той же конфигурации (`mode`, по
  умолчанию live): live — `LLMSettings.from_env` проверяется в `__init__` (отказ до
  ready.json); stub — детерминированный профиль клиента (`CUSTOMER_STUB_PROFILE`,
  data-only, хешируется как версия модели). Seed модели — `derive_seed(invite.seed,
  "traffic", "customer", episode_id)`.
- scripted — прежний путь `CorpusTraffic.client` без изменений.

## Учёт расходов

`LlmUser.summary()` несёт `usage` (tokens/requests модели клиента) и версию модели;
harness пишет это в событие `legal_user` существующим кодом (`flow._play_legal`),
cost-поле не используется — split accounting сторон (NFR-2.9) не затронут. Движок
метрик может пересчитать расходы клиента по сырью позже (та же неизменяемая телеметрия).

## Совместимость

Канал, защита, метрики, расписания и оффлайн-прогон не меняются; контракт traffic-svc
расширяется опциональным полем launch (`client`), отсутствие поля = scripted.
