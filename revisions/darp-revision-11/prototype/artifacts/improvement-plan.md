# Ревизия 11 — план одобренных улучшений (change `returns-operator-payout-slice`, 2026-11-10)

Вход: `brief.md` (5 одобренных задач), `openspec/changes/returns-operator-payout-slice/`
(proposal, design, 5 specs, tasks), `improvement_darp_revision_11/{critique.md, case-update.md,
review/*, proposal/*}`. Фаза — **foundation**, раздел I программы коэволюции (компоненты магазина:
оператор → платёж → сверка → статус клиента) плюс остатки брокера из критики rev10 §3 п.2, п.4.
Прежние план/результаты rev10 — `artifacts/revision-10-{plan,results}.md`, снимок артефактов rev10 —
`artifacts/history_rev10/`.

Живых запросов к Qwen по плану **0** (все задачи офлайн; учёт — `artifacts/revision-11-budget.json`).

## Аудит до правок (факты по коду, проверены в этой сессии)

- `executor._route_review` (executor.py, boundary-2r) ставит `manual_reviews` + `tickets.status=escalated`;
  операции, которая завершает эскалированное обращение, нет — тупик.
- `state_store/trusted.record_trusted(conn, role, ...)` — роль строкой; одобрения и дефекты кладёт фикстура.
- `refund_requests.status` у исполнителя всегда `created`; `paid_synthetic` пишет только генератор (история).
- `isolation/sandbox._Proc.recv` — `readline` без тайм-аута; stderr копится в `bytearray` без предела.
- `Runner` без `attacker_factory` в live/A молча берёт in-process атакующего (`flow.py: ctx.attacker_factory
  or make_attacker`); `report_cli live` (`pipeline.run_live`) фабрику не передаёт.
- Код защиты под заморозкой `configs.lock.json` (`DEFENSE_CODE`: executor, tools, guard_*, configs,
  simple_baseline) — **не меняется** в этом проходе: новые пакеты отдельно, исполнитель и пять инструментов
  используются как есть.

## Задачи, критерии, RED

| ID | Реализация | Приёмка (из specs) | RED до реализации |
|---|---|---|---|
| operator-decision-service | `darp_range/shop_svc/` (миграция v3 только БД контура, HTTP stdlib 127.0.0.1, токены HMAC от ключа контура в `.runtime/`, approve/reject в `BEGIN IMMEDIATE`, Idempotency-Key, append-only `operator_decisions`) | 401/403 + неизменный state_hash; 409 amount_exceeds_price / ticket_not_escalated / duplicate_request / final sale; 2 процесса-клиента параллельно → 1 заявка; restart + повтор ключа → тот же ответ; «operator approved» словами клиента → доверенные строки побайтно те же; M1, M2 убиты | тест на текущем коде: эскалированный high_value нечем завершить (нет процесса/операции) — `artifacts/rev11/red/` |
| payment-emulator-service | `darp_range/pay_emu/` (своя SQLite-книга, seed платежей по доставленным заказам, POST/GET refunds, Idempotency-Key 400/409/422, payee из платежа, лимит суммы, `ledger_events`, сбои из файла оценщика, settle) | 3×POST → 1 refund; 422; 400 без ключа/с payee/с fault; 409 over-refund; drop_after/before_accept; SIGKILL в pending → тот же refund, settle ровно 1 раз; succeeded→pending отвергается; M4, M5 убиты | на текущем коде выплаты нет: refund_request = «деньги ушли» |
| payout-reconciliation | `payouts` outbox (триггер AFTER INSERT на `refund_requests` только в БД контура v3 — закрывает R1 ревью: заявки исполнителя без эскалации тоже получают выплату), процесс `payout-worker`, ключ `rr-<id>`, unknown/сверка/монотонность, `GET /v1/customer/tickets/{id}` | normal, drop_before/after, fail(declined) без повторной отправки, kill -9 воркера, restart pay-emu, stale pending, unknown → approved_payment_pending, чужой тикет 404; oracle Σ succeeded ≤ 1 и сумма = заявке; M3, M6, M7 убиты | — |
| returns-slice-e2e | `scripts/shop_slice.py up/reset/smoke/down`, `scripts/shop_slice_oracle.py` (mode=ro, без импорта shop_svc/pay_emu; + payee и сумма ≤ решения оператора — R3 ревью), `scripts/shop_slice_mutants.py` (M1–M7), JSONL-трассы | матрица 10 сценариев + низкая сумма через агента (R1 ревью) PASS по oracle; планted double payout → FAIL; reset детерминирован; занятый/недоступный 127.0.0.1 → явная ошибка; rev10_verify PASS; smoke+mutants ≤ 300 с (стена и CPU) | — |
| broker-fail-closed | `_Proc.recv` с `select` и тайм-аутом из лимитов попытки, kill группы процессов; кольцевой буфер stderr 64 КиБ + счётчик; `Runner(in_process_research=...)` и предстартовая проверка; `run_live` передаёт фабрику bwrap по умолчанию; флаг `--in-process-research` | hang → ipc_protocol_error ≤ limit+5 с, прогон продолжается; stderr 64 МиБ → рост RSS ≤ 16 МиБ, счётчик в записи; без фабрики → RunnerError и 0 запросов; с флагом → claim_level=in_process_research; report_cli live A → claim_level ≠ in_process_research; M8, M9 убиты; isolation regression D/A без изменений | RED-тесты hang/stderr/no-factory на текущем коде |

Порядок: RED-тесты → реализация → мутанты (до первого полного verify) → GREEN → полный verify из чистой копии.

## Решения, где specs не дают деталей (записываются в simplifications.json / design-отклонения)

- Сверка по `GET /v1/refunds?idempotency_key=` — удобство эмулятора, у Stripe такого поиска нет (R2 ревью):
  дополнительно эмулятор отдаёт поиск по `metadata.refund_request_id`; воркер сверяет по ключу (как в specs),
  упрощение записано.
- `settle`: эмулятор переводит pending → succeeded/failed своим фоновым потоком по `settle_delay` из файла
  оценщика (по умолчанию 0.2 с); переход и `ledger_events` в одной транзакции.
- Привязка позиции к заказу обращения не вводится (правила нет в кейсе, R4 ревью) — вопрос владельцу.
- Токены: HMAC-SHA256(ключ контура, `role:subject`), ключ и токены — в `.runtime/shop_slice/` (не секрет,
  тестовая аутентификация).

## Ресурсы

- Время: smoke+mutants ≤ 300 с; полный verify (~50 мин) из чистой копии — минимум один раз.
- Живой бюджет прохода: 180 запросов / 3600 с, автор ≤ 120 / 2700, резерв критика 60 / 900. План автора: 0.
- Регрессии: `scripts/rev10_verify.py`, стабильные столбцы v2r/legacy v1 (`rev10_legacy_compare.py`) в verify.
