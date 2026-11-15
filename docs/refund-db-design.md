# Дизайн-док: БД сервиса возврата товаров — схема, поля, данные агента

- Issue: [dvarp-prototype#37](https://github.com/CloudNative-urfu/dvarp-prototype/issues/37) (задача 1 из 4: #37 дизайн → #38 реализация → #39 тула агента → #40 README-обоснования).
- Срез кода: `develop` `2c7123d`, 2026-11-15. Ссылки `path:line` актуальны для этого среза.
- Статус: анализ и дизайн, код не меняется. Замороженный defense-код
  (`darp_range/{configs,guard_contract,guard_plugins,guard_policy,executor,tools}/`)
  только читается; правки, требующие замороженных файлов, собраны в отдельный
  список для перезаморозки (§8).
- Смежные обязательства: канон dvarp#17/#18 (метрики очереди E(g)/Q(g), M/M/c),
  dvarp#20 (NFR-2.8 — прогон-микс на едином снимке; контракт СС↔полигон),
  спека #19 (`svc-decomposition`: единый снимок БД, хеш-проверка при
  восстановлении, привязка легальных эпизодов к реальным строкам базы), #36
  (traffic-svc), #38 (CSV-сид + манифест + деплой).

## 1. Роли одной БД

Одна SQLite-БД эпизода (`darp_range/state_store/schema.py:55`) обслуживает
пять потребителей одновременно, и именно это определяет, какие поля обязательны:

| Потребитель | Что берёт из БД | Механизм |
| --- | --- | --- |
| Агент возвратов | заказы/позиции/тикет/историю/правила через 5 тулов | `darp_range/tools/specs.py:56`, `darp_range/executor/executor.py:315` |
| Гвардрейлы | контекст с происхождением (FR-1.6) | `darp_range/guard_contract/types.py:93`, `darp_range/tools/toolbox.py:20` |
| Исполнитель | единственный путь чтения/записи, R1–R7 на границе | `darp_range/executor/executor.py:474` |
| Оценщик | ground truth V/R по диффу состояний, без текста агента (FR-1.18) | `darp_range/evaluator/state.py:118` |
| Аудит/продукт | idемпотентность операций и журнал решений | `darp_range/state_store/schema.py:157`, `darp_range/event_log/` |

Принципиальные ограничения, наследуемые каноном (менять их нельзя):

1. **Роль клиента в БД не хранится** (FR-1.3): разметка legal/attacker живёт в
   `ShopSpec.accounts` вне БД (`darp_range/state_store/generator.py:210`,
   докстринг `darp_range/state_store/schema.py:7`). БД атакующего не отличается
   от БД легального клиента.
2. **Синтетичность** (NFR-2.13): запрещённые колонки
   (`darp_range/state_store/schema.py:177`), почта только `.example`, метка
   `synthetic=1` в `meta`, проверка Луна (`darp_range/state_store/store.py:171`).
   Поля «адрес/телефон/карта», напрашивающиеся в «полноценный» продукт-сервис,
   в этот контрак не попадают.
3. **Детерминизм и хеш-проверка** (NFR-2.1, спека #19): логический хеш
   охватывает DDL всех объектов, `user_version` и все строки
   (`darp_range/state_store/store.py:131`); эпизод стартует только из
   восстановленного и проверенного снимка (`darp_range/state_store/store.py:298`).
   Любое изменение схемы/данных меняет хеш → обязательна перегенерация снимков
   (и перебиндинг корпуса, но сам корпус привязан к БД только архетипами —
   `darp_range/corpus/schema.py:3`, поэтому замороженный корпус переживает
   смену схемы).
4. **Деньги — целые центы, время — модельное ISO-8601 UTC**
   (`darp_range/state_store/schema.py:3`); wall-clock не используется.

## 2. Целевые сущности и поля (схема v3 как цель)

Ниже — целевой перечень сущностей с обоснованием каждого нетривиального поля.
Базовые таблицы v1 (клиенты, заказы, позиции, тикеты, `refund_policy`,
`refund_requests`, `refusals`, `manual_reviews`, `info_requests`,
`ticket_messages`, `ops_ledger`) остаются ядром; доверенные таблицы v2
(`defect_evidence`, `refund_approvals`, `darp_range/state_store/schema.py:218`)
сохраняются как есть — они реализуют «законные исключения правил» доверенным
состоянием (ревизия 6), это правильная модель и для продукта.

### 2.1 `customers`

- Поля: `id, name, email, tier, created_at` (`darp_range/state_store/schema.py:61`).
- Оценка: достаточно. PII-полей (телефон/адрес) нет намеренно — NFR-2.13;
  предикат `pii_disclosed` сверяет текст агента с PII-плейсхолдерами корпуса и
  значениями из `customers`, этих полей ему хватает
  (`darp_range/evaluator/state.py:251`).
- `tier` ни одним правилом R1–R7 не читается (проверка grep по `darp_range/`:
  единственные использования — генератор и схемы). Оставляем как реалистичный
  атрибут продукта; помечаем «не читается правилами» в README (#40), чтобы
  расширители правил не изобретали зависимости от него молча.

### 2.2 `orders` / `order_items`

- Поля: `darp_range/state_store/schema.py:70`, `darp_range/state_store/schema.py:83`.
- Обоснование ключевых полей: `delivered_at` + `status` — окно R1 и запрет
  возврата недоставленного; `total_cents` — витрина заказа; `note` +
  `note_provenance` — недоверенный канал косвенной доставки атак (FR-1.21) с
  явным происхождением `order_data` (`darp_range/state_store/schema.py:79`);
  `final_sale` — исключение R2; `price_cents × qty` — потолок R3 и порог R5
  (`darp_range/executor/executor.py:494`, `darp_range/executor/executor.py:517`).
- Чего не хватает для продукта: ничего обязательного. `placed_at` не участвует
  в правилах (окно считается от доставки), но нужен агенту для витрины
  (`darp_range/executor/executor.py:331`). Стоимость доставки как отдельный
  объект возврата — вне рамок полигона (нет тула и нет операции исполнителя;
  см. §2.9).

### 2.3 `tickets` / `ticket_messages`

- Поля: `darp_range/state_store/schema.py:94`, `darp_range/state_store/schema.py:103`.
- `tickets.customer_id` не избыточно, вопреки внешнему виду: идентичность
  сессии проверяется именно по строке тикета
  (`darp_range/executor/executor.py:261`), а `order_id` может быть NULL
  (обращение без заказа — вид исхода `info_request`/`no_action`).
- `clarifications` — счётчик R7, пишется исполнителем
  (`darp_range/executor/executor.py:370`); сам лимит R7 в схеме не enforced —
  осознанно: это правило процесса, его исполняют агент/гвардрейл `g_proc`
  (`darp_range/executor/executor.py:372`).
- Чего не хватает: `closed_at` (момент финализации). Вид завершения
  восстановим по таблицам решений (`darp_range/evaluator/state.py:77`), но для
  продуктовой витрины и очереди операторов нужен явный момент закрытия.
  Запись делает исполнитель → замороженный файл (см. §8, п. 5).

### 2.4 `refund_policy`

- Поля: `darp_range/state_store/schema.py:115`; тексты R1–R7 и параметры —
  `darp_range/state_store/schema.py:182`.
- Это доверенный источник правил (инструмент 3, `darp_range/tools/specs.py:73`);
  оценка читает её изменение как предикат `policy_tampered`
  (`darp_range/evaluator/state.py:136`). Поля достаточны; параметры границ
  R1/R2/R5 исполнитель обязан брать только отсюда
  (`darp_range/executor/executor.py:470`).

### 2.5 `refund_requests`

- Поля: `darp_range/state_store/schema.py:123`. `UNIQUE(order_item_id)` —
  схемная гарантия R4 («одна заявка на позицию», `darp_range/state_store/schema.py:122`).
- `ticket_id NULL` легитимно: исторические возвраты, созданные генератором
  (`created_by='history'`, `darp_range/state_store/generator.py:201`), не
  привязаны к обращению эпизода.
- Чего не хватает для продукта: жизненного цикла статуса. Сейчас закрытый
  словарь `('created', 'paid_synthetic')` (`darp_range/state_store/schema.py:130`)
  фиксирует только «заявка создана» и «синтетически оплачена». Полноценному
  сервису нужны `approved / rejected / cancelled` и момент решения.
  Отдельные значения пишет исполнитель → заморозка (§8, п. 3).
- `order_id` денормализовывать не рекомендую: оценщик и R5-проверка восстанавливают
  его джойном (`darp_range/evaluator/state.py:150`,
  `darp_range/executor/executor.py:513`), а денормализация потребует править
  INSERT исполнителя (заморозка) ради удобства, которого на текущих объёмах
  (< 90 клиентов, ~450 заказов, `darp_range/state_store/generator.py:81`) нет.

### 2.6 `refusals` / `manual_reviews`

- Поля: `darp_range/state_store/schema.py:135`, `darp_range/state_store/schema.py:143`.
- `UNIQUE(ticket_id)` у `manual_reviews` — «одна ручная проверка на обращение»;
  `refusals` uniqueness обеспечена идемпотентностью финализации через
  `ops_ledger` (`darp_range/executor/executor.py:404`).
- Чего не хватает (главный разрыв для канона dvarp#17/#18): `manual_reviews`
  хранит только `enqueued_at` (`darp_range/state_store/schema.py:147`). Метрики очереди операторов в каноне — доля
  эскалаций E(g) и задержка Q(g) с SLA-членом `P(W_q > Q_SLA)` — требуют
  моментов постановки в очередь **и** обслуживания. Без `decided_at`,
  `decision` (`approved`/`rejected`) и `decided_by` (сейчас роль одобрения
  известна только из `refund_approvals.approved_by`,
  `darp_range/state_store/schema.py:234`) очередь как процесс из БД не
  восстанавливается. Дополнение полей — чисто `state_store` (v3); запись
  решения оператора делает исполнитель/фикстура → заморозка (§8, п. 4).
  Связка уже существует: `refund_approvals` — это «одобрение», а
  `manual_reviews.decision` нужен для исхода «отказано оператором», которого
  сейчас в БД нет вообще (тикет остаётся `escalated` навсегда).

### 2.7 `info_requests`

- Поля: `darp_range/state_store/schema.py:150`.
- Чего не хватает для аудита: текста вопроса. Канонический текст по коду
  существует только в замороженном коде тула
  (`darp_range/tools/specs.py:46`); модель может дать свой текст
  (`darp_range/tools/specs.py:44`), и он фиксируется как реплика агента
  (`ticket_messages`), но сопоставить «запрос сведений #2 = какой именно
  вопрос» из БД нельзя. Добавить nullable `question_text` (v3); пишет
  исполнитель в `_op_request_info` → заморозка (§8, п. 6). Без правки
  исполнителя колонка может заполняться фикстурой/тулом — поэтому понижаем
  приоритет: сначала правка записи, потом колонка.

### 2.8 `ops_ledger`

- Поля: `darp_range/state_store/schema.py:157`. Назначение — идемпотентность
  разовых операций (`final:<ticket>`, `refund:item:<id>`, `info:<ticket>:<n>`,
  `darp_range/executor/executor.py:293`) и аудит внутри БД.
- Полный аудит живёт в EventLog (вне БД): `exec_request/exec_result`,
  `tool_call/tool_result`, решения политики. Для продуктовой БД
  (`#38`: «аудит по ops_ledger») не хватает payload операции: сумма, код
  причины, автор. Часть уже денормализована в таблицах решений
  (`refund_requests.amount_cents/created_by`, `refusals.reason_code`), поэтому
  минимально достаточное дополнение — nullable `detail_json` с фактом операции
  (v3). Пишет исполнитель → заморозка (§8, п. 7); до неё аудит остаётся на
  EventLog, что для полигона корректно (единственный источник правды — журнал
  оркестратора, спека #19).

### 2.9 Осознанно вне схемы v3 (продукт-дорожная карта, не полигон)

| Сущность | Почему не сейчас |
| --- | --- |
| Возвратные поставки/RMA (посылка обратно, трекинг, приёмка на складе) | Ни один tul (`darp_range/tools/specs.py:56`) и ни одна операция исполнителя (`darp_range/executor/executor.py:93`) не работают с логистикой; заводить таблицу, которую никто не пишет/читает — мёртвые данные, меняющие хеш каждого эпизода. |
| Частичные возвраты/обмены | R3 запрещает частичную компенсацию вне правил (`darp_range/state_store/schema.py:188`); оценщик требует точного `price×qty` (`darp_range/evaluator/state.py:298`). |
| Платёжные инструменты/шлюзы | Запрещено синтетичностью (NFR-2.13) и противоречит «возвраты — записи в локальной БД без платёжного шлюза» (`darp_range/state_store/generator.py:8`). |
| Вложения (фото дефекта) | `photo_of_defect` существует только как код вопроса (`darp_range/state_store/schema.py:42`); материальный файл — вне БД, подтверждение дефекта уже моделируется доверенным `defect_evidence` (v2). |
| Поля клиентов для PII-профиля | NFR-2.13, см. §2.1. |

## 3. Индексы

Есть (`darp_range/state_store/schema.py:165`):
`orders(customer_id)`, `order_items(order_id)`, `refund_requests(customer_id, created_at)`,
`ticket_messages(ticket_id, seq)`; в v2 — `defect_evidence(order_item_id, status)`,
`refund_approvals(order_item_id)` (`darp_range/state_store/schema.py:238`).

Целевые добавления (v3, чисто `state_store`, заморозка не нужна):

1. `refund_requests(ticket_id)` — поиск заявок обращения в идемпотентном
   повторе (`darp_range/executor/executor.py:383`) и в оценщике
   (`darp_range/evaluator/state.py:150`); сейчас полное сканирование.
2. `refusals(ticket_id)` и `info_requests(ticket_id)` — дифф/сводка оценщика
   и дедупликация по обращению (`darp_range/evaluator/state.py:163`).
3. `tickets(customer_id, status)` — выборка открытых обращений клиента
   (продуктовый сценарий «список обращений клиента»).
4. `manual_reviews(enqueued_at)` — очередь операторов (после добавления
   `decided_at` — составной `(enqueued_at, decided_at)` для E(g)/Q(g)).

Логический хеш охватывает DDL индексов (`darp_range/state_store/store.py:135`),
так что любые добавления индексов — перегенерация снимков (п. 3 §1); на
поведение вердиктов это не влияет.

## 4. Данные агента: доверенное ↔ недоверенное

Что агент читает через тула (пять, набор закрыт — `darp_range/tools/specs.py:131`):

| Tul | Операция исполнителя | Таблицы/поля | Доверенность |
| --- | --- | --- | --- |
| `read_order` (`darp_range/tools/specs.py:57`) | `read_order` (`darp_range/executor/executor.py:315`) | заказ: `id, placed_at, delivered_at, status, total_cents`; позиции: `sku, title, category, price_cents, qty, final_sale, refund_requested`; заметка | заказ/позиции — доверенные бизнес-данные; `note` — недоверенный текст с явным `note_provenance` (`darp_range/state_store/schema.py:79`), в контекст попадает фрагментом с происхождением (`darp_range/executor/executor.py:330`) |
| `read_ticket` (`darp_range/tools/specs.py:66`) | `read_ticket` (`darp_range/executor/executor.py:336`) | тикет: `status, opened_at, clarifications`; сообщения: `author, provenance, body` | `body` сообщений `customer`/`channel`/`ticket_history` — недоверенный; `author=system` и служебные записи — доверенные |
| `get_refund_policy` (`darp_range/tools/specs.py:72`) | `read_policy` (`darp_range/executor/executor.py:351`) | `refund_policy`: `rule_id, version, text, params` | полностью доверенный (`provenance=trusted_policy`) |
| `ask_customer` (`darp_range/tools/specs.py:78`) | `request_info` (`darp_range/executor/executor.py:361`) | пишет `info_requests`, `tickets.clarifications`, `ops_ledger` | исходящий запрос; ответ клиента придёт недоверенной репликой канала |
| `finalize_case` (`darp_range/tools/specs.py:91`) | `create_refund`/`refuse`/`assign_manual_review` (`darp_range/executor/executor.py:412`, `:551`, `:568`) | пишет таблицы решений + `ops_ledger` | решение агента; доверенность проверки — на границе исполнителя |

Контракт происхождения для гвардрейлов: закрытый словарь `PROVENANCES`
(`darp_range/state_store/schema.py:45`) ↔ `ContextFragment(text, provenance,
source)` (`darp_range/guard_contract/types.py:93`); доверенные
происхождения — только `trusted_policy` и `system`
(`darp_range/guard_contract/types.py:31`). Чужие тексты правил (в репликах
клиента, заметках заказа) агент обязан игнорировать в пользу
`get_refund_policy` — это требование зашито в описание тула
(`darp_range/tools/specs.py:74`).

Что агенту сейчас недоступно и не нужно (проверка FR-1.9 «агент не знает
гвардрейлов»): роли клиентов, пометки атак, `accounts`, доверенные таблицы v2
(их пишет только `system/operator`, `darp_range/state_store/trusted.py:45`),
EventLog и телеметрия (барьер контракта dvarp#20). Доступ агента к чужим
заказам отсечён на уровне запроса `WHERE customer_id = session.customer_id`
(`darp_range/executor/executor.py:318`) с оракул-эквивалентным ответом «нет
такого заказа» (`darp_range/executor/executor.py:321`).

Для задачи #39 (тула) из этого следует: состав полей тулов менять не нужно —
разрывов в данных, нужных для решения R1–R7, нет (заказ даёт окно R1, флаг
R4, потолок R3; политика даёт пороги R5/R7; тикет даёт счётчик R7).
Потенциальные уточнения тулов — только факультативные (например, отдача
`note_provenance` явным полем, а не только внутри `context`), и они касаются
замороженного `tools/`/`executor/`.

## 5. Что нужно оценщику (ground truth V/R)

Оценщик судит только по диффу двух логических дампов + справочным таблицам
итогового состояния (`darp_range/evaluator/state.py:118`). Требования к БД и
их фактическое покрытие:

| Требование | Где в БД | Статус |
| --- | --- | --- |
| Новые заявки возврата: сумма, причина, позиция, владелец | `refund_requests(amount_cents, reason_code, order_item_id, customer_id)`; owner/max_amount восстанавливаются джойном `order_items`/`orders` (`darp_range/evaluator/state.py:150`) | есть |
| Отказы и ручные проверки как решения | `refusals.reason_code`, `manual_reviews.reason_code` | есть |
| Запросы сведений | `info_requests` | есть |
| «Неожиданные изменения» вне таблиц эпизода | `EPISODE_MUTABLE_TABLES` (`darp_range/state_store/schema.py:172`) | есть; при v3 список расширять при добавлении episode-mutable таблиц |
| Порог R3 = price×qty | `order_items.price_cents, qty` | есть |
| Исключение R2 — только доверенный verified-дефект из стартового состояния | `defect_evidence` (v2) | есть (`darp_range/evaluator/state.py:183`) |
| Исключение R5 — одобрение оператора с потолком | `refund_approvals.max_amount_cents` (v2) | есть (`darp_range/evaluator/state.py:186`) |
| Подмена политики | `refund_policy` (дифф) | есть |
| PII-диск | `customers` значения + тела сообщений агента | есть |
| Привязка ground truth к конкретным строкам | корпус символичен (архетипы), конкретные id подставляет bind по seed (`darp_range/corpus/schema.py:3`) | есть; значит, архив архетипов генератора (`darp_range/state_store/generator.py:64`) менять нельзя без перебиндинга |

Ground truth атакующего текста (цели `slow`/`burn`) — раунды и токены из
EventLog, не из БД (`darp_range/evaluator/evidence.py:63`): телеметрия не
входит в БД эпизода и не должна (барьер dvarp#20).

## 6. Аудит

Двухуровневая модель остаётся:

1. **В БД** — факты, обязательные для идемпотентности и восстановления
   процесса: `ops_ledger` (ключи разовых операций) + таблицы решений
   (append-only, авторизатор исполнителя запрещает UPDATE/DELETE —
   `darp_range/executor/executor.py:611`, плюс TEMP-триггеры ревизии 8).
2. **EventLog (вне БД)** — полная трасса: запросы/результаты исполнителя,
   вызовы тулов, сигналы/решения гвардрейлов, канал. Оценщику EventLog отдаётся
   только структурно, без текстовых полей (`darp_range/evaluator/evidence.py:42`).

Разрыв уровня БД — §2.7/§2.8 (текст вопроса, payload в `ops_ledger`,
исходы ручных проверок). Для полигона приёмка «аудит полон» сегодня
выполняется связкой «ops_ledger + EventLog»; продуктовый аудит без EventLog
станет возможен после пунктов 4/6/7 §8.

## 7. Согласование с #19/#36/#38

- **Единый снимок и хеш-проверка** (`darp_range/state_store/store.py:237`,
  `:298`) соответствуют спеке #19: восстановление с проверкой хеша, baseline
  читается read-only, chmod 0444. Изменение схемы v3 → перегенерация снимков
  и, при смене данных генератора, перегенерация корпуса (корпус
  сид-независим, поэтому замороженный jsonl не переписывается — см. §1.3).
- **Привязка легальных эпизодов к реальным строкам** (#36/#19, решение
  тимлида): реализуется манифестом поверх CSV-сида (#38), не новой таблицей
  в БД эпизода: маппинг «эпизод → customer_id/order_id/order_item_id» выводится
  из `accounts` (`darp_range/state_store/generator.py:210`) и архетипов
  (`darp_range/state_store/generator.py:64`). Манифест должен входить в
  `dataset_hash` (хеш считается от содержимого строк —
  `darp_range/state_store/generator.py:98`), иначе снимок и манифест смогут
  разойтись.
- **CSV-сид (#38)**: генератор обязан сохранить детерминизм и синтетичность;
  источник данных (CSV) подменяет только содержимое `CATALOG`/`_BENIGN_NOTES`
  (`darp_range/state_store/generator.py:33`), проверки
  `assert_synthetic` (`darp_range/state_store/store.py:171`) и запрет
  запрещённых колонок остаются обязательными на выходе деплой-скрипта.

## 8. Список расхождений «требуется ↔ есть»

Классификация: **[ok]** — есть и покрывает; **[free]** — недостаёт, правится в
`state_store/` (не заморожен) в #38; **[frozen]** — недостаёт, правка требует
замороженного файла → список для перезаморозки; **[wont]** — осознанно не
делаем (§2.9).

| № | Требование | Есть | Расхождение и место | Класс |
| --- | --- | --- | --- | --- |
| 1 | Сущности клиенты/заказы/позиции/тикеты/правила/заявки/отказы/ручные/запросы сведений/журнал | да | `darp_range/state_store/schema.py:55`–`:163` | ok |
| 2 | Доверенные факты-исключения R2/R5 | да (v2, миграция) | `darp_range/state_store/schema.py:218`, `darp_range/state_store/trusted.py:34` | ok |
| 3 | Происхождение недоверенных текстов (контракт FR-1.6) | да | `darp_range/state_store/schema.py:45`, `:79`, `:108`; `darp_range/guard_contract/types.py:31`, `:93` | ok |
| 4 | Единственный путь записи + append-only история | да | `darp_range/executor/executor.py:610`–`:648`, `:666` | ok |
| 5 | Снимок/хеш/восстановление (спека #19) | да | `darp_range/state_store/store.py:237`, `:264`, `:298`, `:355` | ok |
| 6 | Индекс заявки по обращению | нет | нет `refund_requests(ticket_id)`; повтор/оценщик сканируют таблицу — `darp_range/executor/executor.py:383`, `darp_range/evaluator/state.py:150` | free |
| 7 | Индексы решений по обращению и открытых тикетов | нет | нет `refusals(ticket_id)`, `info_requests(ticket_id)`, `tickets(customer_id, status)` — `darp_range/state_store/schema.py:165` | free |
| 8 | Исходы очереди операторов (E(g), Q(g), SLA — dvarp#18) | частично | `manual_reviews` хранит только `enqueued_at` (`darp_range/state_store/schema.py:147`); нет `decided_at`, `decision`, `decided_by` — очередь из БД не восстанавливается | free (колонки) + frozen (запись) |
| 9 | Жизненный цикл статуса заявки (approved/rejected/cancelled) | нет | закрытый словарь `('created','paid_synthetic')` — `darp_range/state_store/schema.py:130` | frozen |
| 10 | Текст вопроса уточнения в БД | нет | `info_requests` без текста (`darp_range/state_store/schema.py:150`); канонический текст только в коде тула — `darp_range/tools/specs.py:46` | frozen (запись), колонка — free |
| 11 | Payload операций в `ops_ledger` | нет | только `op_key/ticket_id/kind/created_at` — `darp_range/state_store/schema.py:157`; полный факт — в EventLog вне БД | free (колонка) + frozen (запись) |
| 12 | Момент закрытия тикета | нет | `tickets` без `closed_at` (`darp_range/state_store/schema.py:94`); вид завершения восстановим по таблицам решений (`darp_range/evaluator/state.py:77`) | frozen (низкий приоритет) |
| 13 | Манифест привязки легальных эпизодов к строкам (#36/#19) | частично | основа есть (`accounts`/архетипы — `darp_range/state_store/generator.py:210`, `:64`), самого манифеста нет — зона #38 | free (#38) |
| 14 | CSV-сид и деплой-скрипт (#38) | нет | данные зашиты в `darp_range/state_store/generator.py:33` | free (#38) |
| 15 | RMA/логистика, частичные возвраты, вложения, платёжные поля | нет | осознанно вне рамок (§2.9), изменения frozen-поверхности не требуют | wont |
| 16 | Поля ролей/PII в БД | нет | запрещено каноном (FR-1.3, NFR-2.13) — `darp_range/state_store/schema.py:7`, `:177` | wont |

### 8.1 Список для перезаморозки (санкция владельца до правки)

Затронутые замороженные файлы и суть минимальных правок:

1. `darp_range/executor/executor.py` `_op_request_info` (`:361`) — писать
   текст вопроса в `info_requests.question_text` (п. 10).
2. `darp_range/executor/executor.py` — исходы ручных проверок: UPDATE
   `manual_reviews.decided_at/decision/decided_by` (п. 8); потребует
   расширения авторизатора (`:611` — сейчас UPDATE всех таблиц истории
   запрещён) и, вероятно, новой операции роли `operator`.
3. `darp_range/executor/executor.py` `_op_create_refund` (`:412`) —
   расширенные статусы заявок (п. 9) и `ops_ledger.detail_json` (п. 11).
4. `darp_range/executor/executor.py` `_op_create_refund`/`_op_refuse`/
   `_op_assign_manual_review` — запись `tickets.closed_at` (п. 12); альтернатива
   без заморозки: вычислять момент из `ops_ledger.created_at` ключа
   `final:<ticket>` на стороне читателя (рекомендуется, если перезаморозку
   не дают).
5. `darp_range/tools/specs.py` (`:57`–`:105`) — факультативные уточнения
   описаний тулов (§4); не блокируют #38.

Пункты 6–7 и колонки пунктов 8/10/11 правятся только в `state_store/`
(`schema.py`, `generator.py`, `store.py` — не заморожены) и в #38.

## 9. Итог

- Ядро схемы v1/v2 корректно и для полигона, и как каркас продукта: его
  расширяем, а не переписываем; миграция — по образцу `apply_schema_v2`
  (`darp_range/state_store/trusted.py:34`) с `user_version=3` и
  перегенерацией снимков.
- Содержательные разрыва ровно три: аудит очереди операторов (№8),
  жизненный цикл заявки (№9) и продуктовый аудит БД без EventLog (№10/№11);
  остальное — индексы (№6/№7) и манифест/CSV-сид для #36/#38 (№13/№14).
- Для данных агента разрывов нет: состав пяти тулов и их поля уже покрывают
  все решения R1–R7; доверенность обеспечена контрактом provenance и границей
  исполнителя, а не полями БД.
