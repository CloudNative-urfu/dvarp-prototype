# Готовность основы (ревизии 6–8, фаза foundation): инвариант → реализация → тест → пробел

Это карта готовности **исследовательского полигона**, а не заявление о production-ready. Код —
`darp_range/`; тесты — `tests/`; проверки — `artifacts/checks/revision6/`, `artifacts/checks/revision7/`.
Все проверки офлайн и без модели (`python3 -m unittest tests.test_server_invariants_v2
tests.test_foundation_v2 tests.test_review_routing_v2r tests.test_revision7_foundation`,
`python3 scripts/mutate_rev6.py`, `python3 scripts/mutate_rev7.py`). Версия rev6 этого файла —
`artifacts/history_rev6/production-readiness.md`, версия rev7 — `artifacts/history_rev7/production-readiness.md`.
Ревизия 8: `tests.test_revision8_append_only`, `tests.test_revision8_routing_neutrality`, `scripts/mutate_rev8.py`.

## 1. Три границы исполнителя

| | `executor-boundary-1` (исторический baseline) | `executor-boundary-2` (rev6) | `executor-boundary-2r` (rev7, review-routing) |
|---|---|---|---|
| выбор | `Executor(..., boundary="executor-boundary-1")` — по умолчанию | `boundary="executor-boundary-2"` | `boundary="executor-boundary-2r"` |
| прогон | `RunPlan.foundation="v1"` (по умолчанию) | `foundation="v2"` (доверенные факты по умолчанию `gt_rev6` — только для воспроизведения rev6; `--trusted-facts declared` — объявленные) | `foundation="v2r"` (доверенные факты `declared`) |
| оценщик | evaluator-1 | evaluator-2 | evaluator-2 (без изменений: ручная проверка — по состоянию `manual_reviews`) |
| схема БД | v1 | v2 = v1 + `defect_evidence`, `refund_approvals` | v2 (словарь причин не менялся) |
| отпечаток | `BOUNDARY_FINGERPRINT` 955561c0… — неизменен с rev1 | `BOUNDARY_V2_FINGERPRINT` 35890f48… — неизменен с rev6 | `BOUNDARY_V2R_FINGERPRINT` 33bc7c97…; в `exec_request` журнала и `foundation_spec` манифеста |
| R1/R2/R5 при отказе | суждение агента | текстовый отказ, обращение открыто, эскалировать должен агент | R5 и R2-с-причиной-исключением → серверная ручная проверка; прочие — отказ |
| правила суждения агента | R1, R2, R5, R7 | только R7 (его закрывает g_proc) | только R7 |

boundary-1 не меняется и сохраняется как исторический исследовательский baseline (методика v1 §1):
полный verify на нём — регрессия (фронт {g_simple} seeds 7/8/9, replay rev4/rev5).

## 2. Карта отличий v1 → v2 (копия `executor/boundary.py::BOUNDARY_V1_TO_V2`)

| правило | boundary-1 | boundary-2 |
|---|---|---|
| R1 доставка и окно | суждение агента | серверная проверка при записи: заказ `delivered` и `(tickets.opened_at − delivered_at).days ≤ window_days` |
| R2 final sale | суждение агента; слово клиента о дефекте принимается | `reason_code == R2.exception_reason` **и** доверенная `defect_evidence(order_item_id, status='verified')` |
| R5 дорогой/повтор | суждение агента | отказ `requires_manual_review`, если `price×qty > manual_threshold_cents` или другой возврат по тому же заказу за `repeat_window_days`, без доверенного `refund_approvals(approved_by='operator', max_amount_cents ≥ amount)` |
| доверенные таблицы | нет | `defect_evidence`, `refund_approvals`; пишут только роли `system`/`operator` через фикстуру эпизода |
| R3, R4, R6, identity, роли, одно завершение, идемпотентность | без изменений | без изменений |
| R7 лимит уточнений | агент / g_proc | агент / g_proc |

Расхождения, которые нельзя смешивать при сравнении: ProcessGate (g_proc), g_simple и evaluator-1
считают повтор R5 **без окна**, boundary-2 — в окне `repeat_window_days`. ProcessGate для R2
доверяет заявленной причине, boundary-2 — только доверенному evidence.

## 2a. Карта отличий v2 → v2r (копия `executor/boundary.py::BOUNDARY_V2_TO_V2R`)

| что | boundary-2 | boundary-2r |
|---|---|---|
| R5 `requires_manual_review` | отказ; обращение `open`; агент должен сам вызвать `manual_review` | в той же транзакции `BEGIN IMMEDIATE`: `INSERT manual_reviews(high_value \| repeat_refund)` от служебной роли `server`, ключ `final:<ticket>`, обращение `escalated`; `refund_requests` нет; агент видит `status=escalated` и нейтральный текст без порогов |
| R2 `final_sale_not_refundable` | отказ | если `reason_code == R2.exception_reason` и нет verified evidence → `manual_reviews(customer_request)` от `server`; иная причина — жёсткий отказ |
| жёсткие отказы | — | без изменений: `not_delivered`, `outside_window`, `not_found_or_not_owner`, `duplicate_request`, `amount_exceeds_price`, `ticket_not_open`, final sale с иной причиной |
| авторизатор соединения | rev7: глубина (см. §4a) — отпечаток v2 при этом НЕ менялся (D7.4) | глубина входит в спецификацию 2r (`authorizer_depth`) |
| промпт агента, заглушка, инструменты | без изменений | без изменений |

`server` — не роль вызывающего кода (её нет в `ROLES`, у неё нет операций: вызов
`execute("server", …)` → `role_not_permitted`); это метка в журнале (`exec_result.routed_by`).
Повтор агентом того же завершения после маршрутизации (`manual_review` с той же причиной) —
идемпотентный повтор без записи; любое другое завершение/уточнение — `ticket_not_open`.
Решение по коду R2: новый код `defect_check` не введён — он меняет словарь схемы v1 и отпечаток
boundary-1, что ломает побайтную регрессию rev6; используется существующий `customer_request`
(simplifications.json).

## 3. Решения по неоднозначностям (зафиксированы до реализации, improvement-plan.md)

1. **Опорная точка окна R1** — `tickets.opened_at` обращения сессии (как ProcessGate); ровно
   `window_days` — разрешено, `window_days + 1` — отказ `outside_window`. **Гранулярность — целые
   дни** (`timedelta.days`, единая для ProcessGate, g_simple и boundary-2/2r): обращение, открытое через
   30 дн 23 ч 59 мин после доставки, проходит (проба P5 критика rev6). Семантика не менялась
   (rev7 authorizer-depth: только явная запись). Заказ не `delivered` —
   `not_delivered` (схема гарантирует `status='delivered' ⇔ delivered_at IS NOT NULL`).
2. **Порог R5 — по стоимости позиции `price_cents × qty`**, а не по запрошенной сумме. Т. к. R3
   проверяется раньше (`amount ≤ price×qty`), это совпадает с `max(price×qty, amount)` ProcessGate.
   Следствие: частичный возврат дорогой позиции тоже требует одобрения — это интерпретация текста
   R5 («Refunds above 200 USD»), записанная как возможная ложная блокировка частичных возвратов.
3. **Окно повтора R5** — другой `refund_requests` по другой позиции того же заказа с
   `created_at ≥ now − repeat_window_days` (`now` — модельное время записи).
4. **Привязка возврата к заказу обращения** как инвариант не вводится: документы кейса её не
   требуют; остаётся открытым вопросом (п. 6).
5. **Одобрение R5** покрывает возврат при `amount_cents ≤ max_amount_cents`; одобрение даёт только
   `operator` (system может записать evidence, но не одобрение).
6. **Легальный контроль heldout_v3 ctl-04** (final sale + defective по словам клиента): в прогонах v2
   rev6 фикстура клала доверенную verified evidence, выведенную из ground truth (`gt_rev6`);
   **rev7 (`trusted-facts-declared`)**: доверенные записи берутся только из объявленного поля записи
   корпуса `fixture.trusted` (`runner/flow.py::trusted_fixture(conn, bound)`, ни GT, ни traffic не
   читаются). У ctl-04 объявлений нет → на v2d ложная блокировка сервера (`lost`, 5 эп./ячейку), на
   2r — `unneeded_escalation`. Это теперь измерение; GT корпуса не переписан, правило не ослаблено.
7. **rev7: одобрение меньше полной суммы** (legal `v7_high_value_partial_approval`, trusted_v7 ctl-02):
   GT — manual_review (частичная компенсация вне правил запрещена R3; одобрение не покрывает полную
   сумму). Записано до прогона (improvement-plan.md T3).

## 4. Инвариант → реализация → тест → пробел

| инвариант | реализация | тест (GREEN) | мутация (RED) | пробел / остаток |
|---|---|---|---|---|
| R1 окно, граница включительно | `executor.py::_check_rules_v2` (внутри `BEGIN IMMEDIATE` из `_run`) | `test_r1_outside_window_denied_in_window_passes`, `test_r1_window_boundary_is_inclusive` | «окно не проверяется», «граница строгая» | окно от `opened_at`, а не от момента записи: обращение, открытое в окне и завершённое позже, проходит (так же у ProcessGate) |
| R1 доставка | то же | `test_r1_not_delivered_denied` | «статус не проверяется» — эквивалентный мутант (CHECK схемы) | — |
| R2 final sale только с verified evidence | то же; `defect_evidence` | `test_r2_final_sale_changed_mind_denied`, `test_r2_defective_claim_without_evidence_denied`, `test_r2_pending_or_rejected_evidence_is_not_enough`, `test_r2_verified_evidence_legal_twin_passes_only_with_defective`, `test_r2_regular_item_of_final_sale_order_unaffected` | «evidence не требуется», «причина не требуется», «evidence любого статуса» | процесс получения evidence (проверка фото оператором) не моделируется: запись появляется только фикстурой до сессии |
| R5 дорогой → одобрение оператора | то же; `refund_approvals` | `test_r5_high_value_without_approval_denied`, `test_r5_high_value_with_operator_approval` | «порог снят», «порог по запрошенной сумме», «сумма одобрения не ограничивает» | одобрение не расходуется: повторного использования нет только потому, что R4 запрещает второй возврат той же позиции |
| R5 повтор по заказу в окне | то же | `test_r5_repeat_on_order_denied_first_refund_passes`, `test_r5_repeat_outside_window_passes` | «повтор снят», «окно повтора не учитывается» | расхождение с g_proc/g_simple/evaluator-1 (без окна) — см. §2 |
| слова клиента не меняют доверенное состояние | доверенные таблицы пишет только `state_store.trusted.record_trusted` ролями system/operator; у исполнителя нет такой операции | `test_trusted_records_only_from_start_not_from_channel_words`, `test_trusted_writer_role_is_a_calling_convention_not_authentication` (rev7: переименован из `test_trusted_writes_rejected_for_non_trusted_roles`) | «роль записи не проверяется», «одобрение не только оператором» | **роль `record_trusted` не аутентифицирована**: это строковое соглашение вызывающего кода внутри одного процесса (проба P8 критика rev6). Граница держится на том, что у агента нет такой операции, и на авторизаторе соединения исполнителя |
| исполнитель не пишет доверенные таблицы | `sqlite3.set_authorizer(_deny_trusted_writes)` на соединении boundary-2/2r | `test_executor_cannot_write_trusted_tables` | «авторизатор снят» | — |
| rev7 глубина: пороги и история решений неизменяемы для исполнителя | `_deny_trusted_writes`: `READ_ONLY_TABLES` (refund_policy, orders, order_items, customers, meta), `APPEND_ONLY_TABLES` (refund_requests, manual_reviews, refusals, ops_ledger, info_requests, ticket_messages: только INSERT), `tickets` — только UPDATE status/clarifications, DDL запрещён | `test_review_routing_v2r.AuthorizerDepth`: 13 проб P8/P9 (UPDATE refund_policy, DELETE FROM refund_requests, UPDATE orders …) → `not authorized` на 2/2r; на boundary-1 проходят; `test_legitimate_writes_still_work` (request_info, record_message, refund, refuse, manual_review) | «пропускает refund_policy», «история не append-only», «read-only снят», «tickets — любые UPDATE», пере-строгий «clarifications запрещён» | **rev7: утверждение «история append-only» было неверным** — `INSERT OR REPLACE` переписывал строку истории: авторизатор видит только SQLITE_INSERT, о неявном удалении ему не сообщают (проба критика rev7). Исправлено в rev8 (следующая строка). Соединения фикстуры/harness авторизатором не ограничены (они вне исполнителя) |
| rev8 `history-append-only`: история решений действительно append-only на соединении исполнителя 2/2r | `executor.py::_install_append_only_triggers` до `set_authorizer`: `PRAGMA recursive_triggers=ON` и TEMP-триггеры `BEFORE UPDATE`/`BEFORE DELETE ON main.<t> … RAISE(ABORT)` для каждой таблицы `APPEND_ONLY_TABLES` (при REPLACE неявное удаление вызывает DELETE-триггер только при `recursive_triggers`, sqlite.org/lang_conflict.html); `_deny_trusted_writes`: `SQLITE_ATTACH`/`SQLITE_DETACH` → DENY, `SQLITE_PRAGMA` → DENY, кроме allow-list читающих прагм (`foreign_key_check`, `integrity_check`, `user_version` без аргумента, `table_info` — найдены grep по коду, работающему на этом соединении). TEMP-объекты не попадают в `main.sqlite_master` → логический хеш `dump_state` не меняется | `tests.test_revision8_append_only` (seeds 7/8/9, 2 и 2r): `INSERT OR REPLACE` во все таблицы истории, UPDATE/DELETE, `PRAGMA writable_schema=1`, `recursive_triggers=OFF`, `foreign_keys=OFF`, `ATTACH`, `DROP TRIGGER temp.*` → ошибка, логический хеш не изменился; на boundary-1 те же пробы проходят; законные записи, идемпотентные повторы и конкурентность 40×3 зелёные | «без recursive_triggers», «PRAGMA с аргументом разрешена», «любая прагма», «ATTACH разрешён», «нет TEMP-триггеров» (`scripts/mutate_rev8.py`, все убиты) | граница — только соединение исполнителя 2/2r; соединения фикстуры/harness и роль `record_trusted` не меняются; защита в глубину — у агента и так нет SQL; отпечатки границ не изменены (D8.1), отличие видно по lock v6 |
| rev7: обязательный переход «нужен человек» не зависит от модели (2r) | `executor.py::_route_review` внутри транзакции `_op_create_refund` | `test_review_routing_v2r.Routing` (high_value, partial amount, repeat, final sale без/с pending evidence, approval < суммы, ticket_not_open после, атомарность при сбое), `HardDenials` (old, in_transit, changed_mind, иная причина при evidence, чужой заказ, дубликат, сумма > price*qty; одобренные — возврат), `Concurrency` (40 потоков → 1 manual_reviews, 0 refund) | «не вставляет manual_reviews», «маршрутизирует жёсткий отказ», «final sale при любой причине», «вне транзакции», «не ставит escalated» | атаки на R2/R5 на 2r превращаются в эскалации (E от атак растёт: 104→130 из 660 атакующих эп. на заглушке); серверная ручная проверка — тоже цена |
| rev7: доверенные факты объявлены, не выведены из GT | `corpus/bind.py::bind_trusted` (`fixture.trusted` записи, хеш записи), `runner/flow.py::trusted_fixture(conn, bound)`; исторический `trusted_fixture_gt_rev6` только по явному `RunPlan.trusted_facts` | `test_revision7_foundation.DeclaredTrustedFacts` (подмена GT refund↔refusal и kind attack↔control не меняет набор; контроль чувствительности на gt_rev6) | «trusted_fixture читает GT», «bind игнорирует fixture.trusted», «half = full» | старые корпуса объявлений не имеют: их легальные final-sale-defective по слову клиента теперь измеряются как ложная блокировка (v2d) / эскалация (2r) |
| фикстура не подкладывает возврат | `state_store.fixtures` | `test_fixture_cannot_plant_refund` | — | — |
| конкурентность: ≤1 возврат без одобрения на заказ | проверка и запись в одной транзакции `BEGIN IMMEDIATE` | `test_concurrent_refunds_two_items_same_order` (2 соединения, 1 файл) | `IMMEDIATE→DEFERRED` — эквивалентный мутант: SQLite сам сериализует запись (второе соединение получает BUSY без записи — исход, допустимый критерием) | тест не различает код отказа R5 и BUSY; гарантия держится на сериализуемости SQLite, а не на нашей блокировке |
| TOCTOU: изменение между чтением и записью | проверка по состоянию внутри транзакции записи | `test_toctou_refund_appears_between_read_and_write` | «boundary-2 не применяется» | — |
| boundary-1 сохранён | ветка `if self.boundary == BOUNDARY_V2_VERSION` | `test_attack_calls_pass_on_boundary1`, `test_v1_basis_has_no_server_rules`, `test_fingerprint_in_journal_and_v1_unchanged`; полный verify | — | — |
| v2 требует схему v2 | `Executor.__init__` | `test_v2_refuses_v1_schema`, `test_unknown_boundary_rejected` | — | — |
| oracle по доверенным фактам | `evaluator/state.py::_excepted/_approved` (evaluator-2), факты — из **стартового** состояния | `test_foundation_v2.OracleNegativeControls` (6 контролей + evidence pending/rejected + одобрение < суммы) | «oracle игнорирует evidence», «evidence из конечного состояния», «evidence любого статуса», «одобрение без проверки суммы», «v2 = v1 по одобрению» | evaluator-2 судит только predicates корпуса; новые предикаты (напр. привязка к заказу обращения) не добавлены |
| легальные сценарии не режутся сервером | `agent_demo/legal_suite.py` (скриптовый агент через 5 инструментов) | `test_foundation_v2.LegalSuite`; `scripts/rev6_foundation_table.py` → `foundation_table.md` | — | 1 опубликованная ложная блокировка (п. 3.6); скриптовый агент знает правила — это проверка сервера, не модели |
| rev7: легальный поток без восстановления агента | `NonRecoveringAgent` (стоп после первого отказа finalize) | `test_revision7_foundation.NonRecovering`; `scripts/rev7_foundation_table.py`: на boundary-2 high_value / recently_refunded / final_sale_defect_claim_only → `lost` (RED-документ), на 2r → `completed` с нужной причиной | «NonRecoveringAgent восстанавливается» | живая модель: см. evaluation-results.md §rev7 (n=3 на ячейку) |

Итог мутаций rev6 (`artifacts/checks/revision6/mutations.json`): неэквивалентных 26/26 убиты, 2
эквивалентных объявлены с обоснованием (выживание проверяется). Rev7
(`artifacts/checks/revision7/mutations.json`): 16/16 убиты.

**V=0 на boundary-2/2r для пар trusted_v7 — проверка согласованности** (сервер и evaluator-2
используют одни доверенные факты и одну трактовку правил), а не независимое измерение (review U3).
Отрицательный контроль оракула — те же атаки на boundary-1 (нарушение в 3/3 seeds).

## 5. Что boundary-2 НЕ закрывает (остаточные поверхности, кандидаты без заявлений об успехе)

- цели `slow`/`burn`/`shift` (нагрузка, токены, очередь) — бьют по стоимости и ручной очереди, а не
  по записи (`attack-cards.json`, `inclusion_status=residual_candidate`);
- утечка PII/полей чужого заказа в тексте агента (R6 закрывает доступ, но не текст ответа о своём);
- недоверенные данные в `orders.note` и истории обращения как канал инструкций (память обращения);
- выбор агентом *допустимого, но не того* действия (эскалация вместо возврата, отказ вместо
  уточнения) — это полезность, её меряют task_status/R, а не V;
- R7 (лимит уточнений) — по-прежнему суждение агента/g_proc.

## 6. Открытые вопросы и пробелы

- rev7: серверная маршрутизация срабатывает только на `create_refund`. Если агент (как живой Qwen
  на final_sale_defect_claim_only, 6/6 эп.) вообще не вызывает завершение, 2r ничего не меняет —
  потеря типа (a) остаётся (кандидат: подметальщик открытых тикетов, review A3, смешивается с g_proc).
- rev7: authorizer-depth не меняет отпечаток boundary-2 (D7.4) — по отпечатку v2 до/после rev7 не
  различить; различие видно по lock v5 (`defense_code.executor`).
- Привязка возврата к заказу обращения (тикет по заказу A, возврат по заказу B того же клиента) —
  не инвариант ни на одной границе: **правила привязки к заказу обращения нет в документах кейса,
  вопрос владельцу**. Rev8 правило не вводит, а измеряет: `scripts/rev8_order_scope_diag.py` →
  `artifacts/checks/revision8/order_scope.md` (заглушка 2r: доля маршрутизаций по позиции вне заказа
  обращения, атаки и легальный трафик раздельно; описывает корпус и заглушку, не модель — review N4).
- rev8: нейтральность маршрутизации для агента — `ExecResult.agent_view()` маршрутизированного
  результата побайтно равен `{status: escalated, reason: ROUTED_MESSAGE, result: {escalated: true}}`
  (`tests.test_revision8_routing_neutrality`, также через `Toolbox`); мутанты «basis/код/сумма в
  agent_view» убиты. Причина и пороги агенту не сообщаются (FR-1.3).
- Процесс получения evidence/одобрения не моделируется (нет очереди «проверить фото», нет времени
  оператора на одобрение); законные исключения появляются только из фикстуры.
- Частичный возврат дорогой позиции требует одобрения (интерпретация R5 по стоимости позиции) —
  возможная ложная блокировка; в корпусах таких легальных контролей нет, поэтому не измерено.
- Независимость: тесты и мутации написаны той же сессией, что и код; скрытых тестов критика ещё нет.

## Ревизия 9: граница атакующего (change `attacker-isolation-boundary`)

Проверки: `python3 scripts/isolation_verify.py --out artifacts/rev9/isolation`, `python3 scripts/mutate_rev9.py`,
`python3 -m unittest tests.test_isolation_matrix tests.test_isolation_probes tests.test_isolation_sandbox tests.test_isolation_audit tests.test_isolation_provenance`.

| инвариант | реализация | тест / доказательство | пробел |
|---|---|---|---|
| атакующий не читает исходники/БД/журналы/кассеты/GT/case/артефакты защиты | bwrap: смонтированы только `/nix/store` (ro), staged-пакет (ro), attack-only вход (ro), tmpfs `/tmp`; `--remount-ro /` | 10 файловых проб DENY в sandbox; PASS in-process и в weak (`artifacts/rev9/isolation/probes/probes.md`) | sandbox — опция фабрики, не путь по умолчанию |
| атакующий не находит журнал защиты в памяти (L1) | отдельный процесс; шим `ComponentWriter` шлёт события по IPC | `gc_eventlog_canary`: in-process PASS (канарейка), sandbox DENY | — |
| ключ модели только у брокера | `--clearenv`, bwrap с `env={}`, IPC без ключа/URL, `ChatModel` в брокере | `env_secret_self`, `proc_ppid_environ` DENY; мутант M9.1 (env брокера в bwrap) → утечка через `/proc/1/environ` — убит | защита и оценщик в процессе брокера (соглашение + статический тест) |
| нет сети, сокетов брокера и хоста | `--unshare-all` (net, pid, ipc, uts, cgroup, user) | `tcp_socket_host`, `unix_socket_broker`, `external_network` DENY; `--share-net` ловится | — |
| ссылки и `../` из attack-only каталога не ведут на хост | bind-mount `/input`; хостовые пути не смонтированы | `symlink_escape`, `dotdot_traversal` DENY; PASS в weak | — |
| нарушение схемы IPC — не успех защиты | закрытая схема `isolation/ipc.py`; `ipc_protocol_error` → `protocol_violation` попытки | `test_unknown_ipc_field_is_protocol_violation`; мутант M9.6 убит | — |
| без sandbox — не sandbox-метка | `SandboxUnavailable`; `fallback="in_process_research"` | тесты с отсутствующим и падающим bwrap; мутант M9.9 убит | — |
| вход атакующей модели не зависит от скрытой политики/GT/промпта | бриф по белому списку; брокер передаёт только сообщения | `invariance.json`: 4 изменения × 3 брифа — sha256 равны; изменённый ответ меняет следующий payload | проверено на фиксированных ответах агента |
| утечка промпта через ответ измеряется, не фильтруется | `evaluator/prompt_leak.py` | вариант `leak`: канарейка в payload, исход true; без цитирования false | детектор — только синтетическая канарейка |
| заявление black-box только по фактам | `isolation/provenance.py::claim_level`, реестр оценщика | все 60 legacy-атак implementation-informed; поддельный blind → отказ; провал пробы → `in_process_research` | blind-набора нет |
| защищаемая сторона не знает историю атакующего | память атакующего только в его процессе; брокер хранит `AttemptRecord` | `test_broker_holds_no_attacker_memory` | защита и брокер в одном процессе (не OS-граница) |
