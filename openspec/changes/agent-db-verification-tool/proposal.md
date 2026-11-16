# agent-db-verification-tool (тула агента под схему v3)

## Why

Тимлид (2026-11-15): агенту нужен доступ к внутренней БД прогонов для проверки
реальности возврата — покупал ли клиент раньше, история заказов и возвратов.
Сейчас `read_order` отдаёт один заказ по id, а «список моих заказов» агенту
недоступен. Дизайн-док `docs/refund-db-design.md` (§4, PR #41) установил, что
разрывов в данных для решений R1–R7 нет — требуется только это расширение.

Канон FR-1.11 фиксирует ровно 5 инструментов; состав меняется только через
`canon-requests/`. Заявка `canon-requests/001-fr-1-11-db-verification-tool.md`
предлагает два варианта; в этой задаче принят **вариант 2 — расширение
`read_order`** (пункт заявки: до принятия допускается реализация поверх текущего
набора без правки канона). Число тулов не меняется; запись о расширении
семантики — в «Отклонения канона» репозитория `dvarp` при следующей
синхронизации.

## What Changes

- **`read_order` (вариант 2)**: ответ дополняется `purchase_history` — все заказы
  сессии-клиента (R6: `WHERE customer_id = session.customer_id`) с позициями и
  флагами заявок; проверка «клиент реально покупал» и повторов возвратов — по
  этой истории. Оракул существования отсутствует, как раньше (чужой/
  несуществующий заказ — одинаковый отказ). Факультативное уточнение §4:
  `note_provenance` отдаётся явным полем.
- **Запись в v3-поля аудита** (`executor`, только схема v3; на v1/v2 поведение
  прежнее — парити #19):
  - `_op_request_info` пишет `info_requests.question_text` (текст уточнения от
    слоя тулов, ≤ 500 символов);
  - новая операция `resolve_review` роли `operator` закрывает эскалированное
    обращение: `manual_reviews.decided_at/decision/decided_by`, тикет
    `resolved`/`refused`, `tickets.closed_at`; прочие роли — `role_not_permitted`;
  - `_op_create_refund` пишет `ops_ledger.detail_json` и статус `approved`, когда
    R5-порог покрыт доверенным одобрением оператора (иначе `created`);
  - `tickets.closed_at` пишется при любой финализации (исполнитель, а не чтение
    из `ops_ledger` на стороне читателя).
- **Авторизатор/триггеры**: UPDATE `manual_reviews` разрешён только решённым
  колонкам (`REVIEW_DECIDED_COLUMNS`); `tickets` — status/clarifications/closed_at;
  TEMP-триггер `manual_reviews` на v3 пропускает только решённые колонки.
- **Промпт и спека тулов**: `refund-agent-prompt-5` (шаг 1 — верификация покупки
  по `purchase_history`), `refund-tools-2` (описание `read_order`).
- **Права доступа** описаны в `darp_range/state_store/README.md` (к задаче #40).
- Not changed: число инструментов (5), CLOSED словари решений, политику
  заморозки (defense-код правится свободно, NFR-2.10 — после финализации,
  issue #45).

## Capabilities

### Modified Capabilities
- `state-store-v3`: запись в v3-поля аудита активирована на стороне исполнителя
  (схема не меняется — она создана в `state-store-v3-csv-seed-deploy`).

## Impact

- `darp_range/executor/executor.py`, `darp_range/executor/boundary.py`
  (роль `operator`, операция `resolve_review`, отпечатки границ меняются —
  defense-код правится свободно);
- `darp_range/tools/specs.py`, `darp_range/tools/toolbox.py` (описание
  `read_order`, передача `question_text` в исполнителя);
- `darp_range/agent/prompt.py`; `darp_range/state_store/README.md`;
  `canon-requests/001-...md` (статус: принят вариант 2);
- новые тесты `tests/test_issue39_db_tools.py`.
