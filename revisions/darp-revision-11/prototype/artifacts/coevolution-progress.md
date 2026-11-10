# Прогресс программы коэволюции DARP (исполнитель rev11, стадия integrate, 2026-11-10)

Change `returns-operator-payout-slice` реализован (`artifacts/openspec-results.json`: 5/5 implemented по оценке
автора; финальную оценку даёт критик). Сводка проверок: `artifacts/rev11/fast/summary.md`; полный verify из чистой
копии — `artifacts/checks/revision11_verify/`. Живых запросов: 0. Версия rev10 этого файла —
`artifacts/history_rev10/coevolution-progress.md`.

## I. Компоненты магазина и жизненный цикл возврата

| Компонент | Текущая реализация | API / хранилище | Права | Проверенные сбои | Сквозное доказательство | Остаток |
|---|---|---|---|---|---|---|
| I.1 Данные и сервисные границы | **запущено**: отдельная БД контура (SQLite, схема v3 только для неё) переживает restart; reset из seed с эталонным логическим хешем; БД эпизодов runner остаются v2 | shop-svc HTTP на 127.0.0.1 (`/v1/operator/...`, `/v1/customer/...`), ready.json, JSONL-трассы | токен HMAC контура, роль/субъект из токена, не из тела | restart shop-svc, занятый порт, нет loopback → явная ошибка | `smoke`: `restart_shop_svc`, `reset_deterministic`, `busy_port_up`; `artifacts/rev11/logs/lifecycle.log` | инструменты агента работают с БД контура in-process (не через HTTP); IAM нет |
| I.2 Обращения, вложения, согласования | **запущено частично**: approve/reject эскалированного обращения v2r только аутентифицированным оператором, перепроверка escalated/владельца/R1/R3/R4/final sale в одной транзакции; слова клиента о согласовании не меняют доверенных строк | `operator_decisions` append-only, `refund_approvals(approved_by=operator)` | оператор — approve/reject; клиент — 403; без токена — 401 | 401/403/409 без изменения state_hash; 2 параллельных approve → 1 заявка; повтор ключа → сохранённый ответ | `legal_approve`, `reject`, `operator_api_negatives`, `concurrent_approve`, `duplicate_approve`, `claimed_approval_in_chat`; M1, M2 убиты | **вложений как объектов нет** (`defect_verified` — утверждение оператора); привязка позиции к заказу обращения не проверяется |
| I.3 Платёжный эмулятор | **запущено**: отдельный процесс pay-emu со своей книгой (append-only `ledger_events`), Idempotency-Key 400/409/422, получатель только payee платежа, лимит суммы по платежу, сбои только из файла оценщика | `POST /v1/refunds`, `GET /v1/refunds/{id}`, `GET /v1/refunds?idempotency_key=` | поле получателя/`fault` в запросе → 400 | тройной POST → 1 возврат; drop_before/after_accept; restart в pending → тот же возврат, settle ровно один раз; succeeded→pending отвергнут | `payment_emulator_contract`, `restart_pay_emu`; M4, M5 убиты | подмножество модели провайдера; срок жизни ключа не моделируется |
| I.4 Асинхронные события и сверка | **частично**: только опрос и сверка (outbox `payouts`, ключ `rr-<id>`, `unknown`, монотонные CAS-переходы); push-событий нет | `payouts` в БД контура | процесс payout-worker | drop_after_accept → unknown → сверка; fail(declined) без повторной отправки; kill -9 воркера; stale pending после succeeded не применяется | `drop_*`, `fail_declined`, `kill_payout_worker`; M3, M6, M7 убиты | подписанные события с дублями/перестановкой/задержкой — следующий срез |
| I.5 Физический возврат | не выбран: в документах кейса нет правила | — | — | — | — | вопрос владельцу |
| I.6 Сквозной срез | **запущено**: `shop_slice.py up/reset/status/down/smoke`; легальный путь без LLM: high_value → пять инструментов (исполнитель v2r) → серверная эскалация → approve по HTTP → выплата → клиент `refunded` → тикет `resolved` | процессы pay-emu → shop-svc → payout-worker | — | матрица из 10 обязательных сценариев + низкая сумма через агента + 3 проверки | `artifacts/rev11/fast/summary.json` (smoke 17/17, мутанты 9/9, ≤ 300 с) | живой срез через агента Qwen на контуре — не выполнялся |
| Ручная очередь (FR-1.15) | модель времени (`manual_queue/model.py`); не связана с `operator_decisions` | — | — | режимы нагрузки | verify | связать время обслуживания очереди с решениями оператора контура |

**Ставший возможным бизнес-переход:** эскалированное обращение v2r → решение аутентифицированного оператора →
заявка → выплата по книге эмулятора → клиентский статус `refunded` / `payment_issue` / `approved_payment_pending`,
с восстановлением после таймаутов и restart без второй выплаты. Раньше цепочка обрывалась на `escalated`.

**Фикстуры и модели, а не сервисы:** согласования эпизодов runner (основа v2r) по-прежнему кладёт фикстура
(`record_trusted`), очередь — модель времени, агент контура — скриптовый.

**Что мешает следующему шагу:** нет вложений-объектов (I.2) и push-событий (I.4); инструменты агента не
переведены на API контура; живого среза в контуре нет; паспортов атак на новые поверхности (подмена approval,
replay события, дубль после таймаута) пока нет — поверхности появились только сейчас.

## A/B/C

| Пункт | Статус | Код / доказательство | Ближайшая зависимость |
|---|---|---|---|
| A1 модель угроз, матрица доступа | без изменений rev10: implemented для атакующего, defender→attacker_memory — convention | `configs/isolation/access_matrix.json` | OS-изоляция защитника |
| A2 процессы, OS-проверки | partial (без изменений) | `darp_range/isolation/` | процесс защиты |
| A3 трасса, payload | implemented; **rev11**: зависший ребёнок → ipc_protocol_error за ≤ limit+5 с и kill группы, прогон продолжается; stderr — хвост 64 КиБ + счётчик | `darp_range/isolation/sandbox.py`, `tests/test_rev11_broker_fail_closed.py`, M8 | затраты атакующего брокерским временем |
| A4 provenance, claim_level | partial; **rev11**: live A без фабрики и без явного `in_process_research` → RunnerError до первого запроса; `report_cli live` A по умолчанию bwrap | `darp_range/runner/orchestrator.py`, `darp_range/report_cli/pipeline.py`, M9 | blind held-out критика |
| A5 наблюдения клиента | partial; **rev11**: появился доверенный read-only статус обращения клиента (`GET /v1/customer/tickets/{id}`) — основа для strict-наблюдений B2 | `darp_range/shop_svc/service.py` | подключение к каналу атакующего |
| B1–B5 | pending | — | A2 (защитник), хранилища |
| C1–C6 | partial (метрики, без изменений rev10) | `artifacts/checks/revision11_verify/e2e/report.md` | живой L* на v2r |

## Ограничения
Один Qwen 9B в обеих ролях. Режим strict-black-box не подтверждён. Срез rev11 офлайн, 0 запросов к моделям.
