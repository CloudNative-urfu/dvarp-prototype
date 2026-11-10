# Ревизия 11 — результаты одобренных улучшений (change `returns-operator-payout-slice`, 2026-11-10)

## Поправка D11-S1 — 2026-11-11

В рамках `payout-reconciliation` исправлена сверка последнего принятого POST после
SIGKILL при `attempts=max_attempts`: GET выполняется независимо от бюджета новых
POST. Отдельный `tests/test_rev11_payout_recovery.py`: RED 4 FAIL → GREEN 4 PASS;
точечная регрессия 9 PASS. Подтверждены восстановление pending/succeeded, отсутствие
новых POST при отсутствующем ключе и единственность выплаты. Evidence и точные
команды: `artifacts/openai-fix/README.md`, `red.log`, `green.log`, `regression.log`.
Предыдущие полные verify не проверяли этот фикс; VM-проверка и независимый re-review
ожидаются от координатора. Это результат только D11-S1, не закрытие всей критики.

План — `artifacts/improvement-plan.md`; результаты rev10 — `artifacts/revision-10-results.md`, снимок артефактов
конца rev10 — `artifacts/history_rev10/`. Живых запросов к Qwen: **0** (`artifacts/improvement-budget.json`).
Быстрая проверка change: `python3 scripts/rev11_verify.py --out artifacts/rev11/fast` → **PASS** (97 с);
полный verify из чистой копии — `artifacts/checks/revision11_verify/` (см. последний раздел).

| ID | Код | Тест / проверка | Результат | Остатки |
|---|---|---|---|---|
| operator-decision-service | `darp_range/shop_svc/{service,server,auth,migrate}.py`, `darp_range/svckit/http.py` | `tests.test_rev11_shop_slice` (TestSliceAuth, MigrationScope); smoke `legal_approve`, `reject`, `operator_api_negatives`, `concurrent_approve`, `duplicate_approve`, `restart_shop_svc`, `claimed_approval_in_chat`; мутанты M1, M2 | PASS; 401/403/409 при неизменном state_hash; 2 процесса-клиента → 1 заявка; restart + тот же ключ → тот же ответ, 0 строк; слова клиента о согласовании не меняют `refund_approvals`/`operator_decisions`/`defect_evidence`; M1, M2 убиты | токен синтетический (не IAM); вложений нет; привязка позиции к заказу обращения — вопрос владельцу |
| payment-emulator-service | `darp_range/pay_emu/{server,ledger,__main__}.py` | `PayEmuContract`, `LedgerMonotonic`; smoke `payment_emulator_contract`, `restart_pay_emu`; M4, M5 | PASS по всем 9 пунктам приёмки + `fault` в запросе → 400; M4, M5 убиты | подмножество модели провайдера; без срока жизни ключа |
| payout-reconciliation | `darp_range/shop_svc/worker.py`, триггер outbox в `migrate.py`, `service.customer_status` | smoke `drop_before_accept`, `drop_after_accept`, `fail_declined`, `kill_payout_worker`, `low_value_agent`, `legal_approve` (stale pending); M3, M6, M7 | PASS; oracle: Σ succeeded ≤ 1 и сумма = заявке во всех сценариях; клиент не видит `refunded` без succeeded; M3, M6, M7 убиты | только опрос; push-события I.4 — следующий срез |
| returns-slice-e2e | `scripts/shop_slice.py`, `scripts/shop_slice_oracle.py`, `scripts/shop_slice_mutants.py`, `scripts/rev11_verify.py` | smoke 17/17 (10 обязательных сценариев + низкая сумма через агента + 3 вспомогательных + `reset_deterministic`, `busy_port_up`, `oracle_negative_control`); `OracleIndependence`, `LoopbackUnavailable`; `rev10_verify` | PASS; smoke стена 15.9 с / CPU 4.7 с, мутанты 70.5 с / 11.2 с, вместе 86 с ≤ 300 с; rev10_verify PASS (120 с); журнал жизненного цикла `artifacts/rev11/logs/lifecycle.log`; трассы JSONL — `artifacts/rev11/fast/smoke/<сценарий>/traces/` | агент скриптовый; живой срез не выполнялся |
| broker-fail-closed | `darp_range/isolation/sandbox.py`, `darp_range/runner/orchestrator.py`, `darp_range/report_cli/{pipeline,__main__}.py` | `tests.test_rev11_broker_fail_closed` (6 тестов), M8, M9; регрессия sandbox ↔ in-process в rev10_verify | PASS; hang → ipc_protocol_error ≤ limit+5 с; 64 МиБ stderr → RSS ≤ 16 МиБ и счётчик; без фабрики → RunnerError, 0 запросов; report_cli live A → bwrap; M8, M9 убиты | затраты атакующего брокерским временем и закрытая схема data — не в этом проходе |

## Изменения вне плана (стадия integrate) и их обоснование

1. **`darp_range/svckit/http.py`: самоподключение после bind.** Тест `LoopbackUnavailable` (приёмка «up на
   недоступном 127.0.0.1 → явная ошибка, не пропуск») падал по тайм-ауту: в netns с опущенным `lo` bind на
   127.0.0.1 проходит, но соединения невозможны (проверено: `connect` → ENETUNREACH), и pay-emu молча ждал запросов.
   Исправление первопричины: сервер после bind соединяется сам с собой; неудача → `LoopbackUnavailable`, код 3.
   После правки модуль тестов контура 13/13 OK, smoke и мутанты без изменений.
2. **`scripts/rev10_verify.py`: проверка бюджета rev10.** Проверка требовала, чтобы `improvement-budget.json`
   совпадал с копией rev9; бриф rev11 требует вести этот общий файл, поэтому проверка падала не из-за дефекта,
   а из-за смены ревизии. Инвариант rev10 («в rev10 общий файл не переписан») теперь проверяется по архиву конца
   rev10 `artifacts/history_rev10/improvement-budget.json` (хеш = `revision-9-budget.json`). Записано в
   `simplifications.json`.
3. **`scripts/verify_e2e.py`: шаг `rev11_verify`** (`--skip-rev10`, регрессия rev10 — отдельный шаг выше).
4. **`scripts/rev11_status.py`**: сборка `status.json` от снимка rev10 (идемпотентно) + копия доказательств verify.

## Регрессии и прежние результаты

- Исполнитель агента, пять инструментов, конфигурации под заморозкой `configs.lock.json` не менялись.
- БД эпизодов runner не мигрируются (тест `MigrationScope`); главная таблица v2r и legacy v1 проверяются шагами
  verify (`primary_foundation`, `legacy_v1_regression`).
- Исторические результаты и прогоны не перезаписаны; новые — в `artifacts/rev11/`, `artifacts/checks/revision11_verify/`.

## Полный verify из чистой копии

См. раздел «Полный verify» в `artifacts/report.md` и `artifacts/checks/revision11_verify/verify.md`.
