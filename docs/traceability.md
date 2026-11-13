# Матрица трассируемости: требования рамки ↔ фабричный каркас

Соответствие между требованиями полигона (`dvarp/docs/guardrails_requirements.md`,
`guardrails-research-design.md`) и текущим каркасом (`dvarp-prototype`, снимок
ревизии 11 после чистки issue #2; строки обновлены по смерженным PR #17, #25, #26).
Навигация по модулям, тестам и CLI — `docs/repo-map.md`; состав и отличия замороженных
конфигураций — `docs/configs.md`. Статусы:

- **covered** — реализовано и проверяется тестом/шагом `verify.py`;
- **covered-heavy** — реализовано, но каркас избыточен для этой строки (упрощение возможно);
- **gap** — не реализовано / не подключено (у строки — issue на доске);
- **factory-extra** — фабричная обвязка за пределами рамки; решение зафиксировано.

Проверка — ссылка на тесты, probes или шаги `scripts/verify.py` (если шаг есть).
Тесты ядра `test_executor`, `test_channel`, `test_guard_policy`, `test_guard_contract`,
`test_state_store`, `test_evaluator`, `test_metrics`, `test_tools` при итеративной работе
запускаются быстрым набором `python3 scripts/dev_smoke.py` (~2 мин, PR #17); полный набор —
`unittest discover` и `scripts/verify.py`.

## FR-1.1–1.4. Архитектура и канал

| Требование | Покрытие | Проверка | Статус |
| --- | --- | --- | --- |
| FR-1.1 Двухсторонняя изолированная схема | `attackers/`, `isolation/`, `runner` (парные прогоны) | `test_attackers`, isolation-тесты, probes `attackers` | covered |
| FR-1.2 Единый текстовый канал | `channel/` (барьер, лимит реплик, D/A) | `test_channel`, probe `channel` | covered |
| FR-1.3 Барьер интерфейса (в обе стороны) + маркированный research-mode | барьер в `channel`, provenance атак, аудит импортов, `research-mode` отдельная метка | `test_isolation_provenance`, `test_isolation_audit`, `test_isolation_sandbox`, аудитор в `acceptance.check` | covered |
| FR-1.4 Единый интерфейс режимов D/A | `attackers` D (корпус) / A (адаптивный), общий вход через канал | `test_channel` (echo D/A), `test_attackers` | covered |

## FR-1.5–1.9. Гвардрейлы

| Требование | Покрытие | Проверка | Статус |
| --- | --- | --- | --- |
| FR-1.5 Плагины с единым контрактом | `guard_contract/` (контракт, реестр, хост) + `guard_plugins/` | `test_guard_contract`, `test_guard_plugins`, probe `guard_contract` | covered |
| FR-1.6 Вход: текст, контекст с происхождением, доверенная задача, предложенный вызов | `GuardInput(text, task, context, proposed_call)`, `context-provenance` | `test_guard_plugins` (stages input/context/tool), `test_guard_policy` | covered |
| FR-1.7 Сигналы allow/flag/block + журнал затрат | сигналы в контракте, затраты гвардрейлов в `event_log` | `test_guard_contract`, `test_guard_policy`, acceptance (guard costs) | covered |
| FR-1.8 Пассивность: нет записи в БД и бизнес-решений | контракт хоста запрещает запись; решение — политика, исполнение — код | контракт + `test_guard_policy` | covered |
| FR-1.9 Подключение без правки агента | конфигурация — данные (`configs/`, fingerprint), `agent factory` не зависит от набора | `test_configs`, `test_agent` | covered |

## FR-1.10–1.14. Процесс и исполнитель

| Требование | Покрытие | Проверка | Статус |
| --- | --- | --- | --- |
| FR-1.10 Возвраты на синтетических данных | `state_store` (синтетическая SQLite-БД магазина) | probe `runner`, `test_state_store` | covered |
| FR-1.11 Ровно 5 инструментов | `tools/` (get_order, ticket, rules, ask, finalize) | `test_tools` (+ `tools specs`), probe `tools` | covered |
| FR-1.12 Свободный текст + сравнение структурированной формы | структурная форма — `simple_baseline` (формы, грамматика) участвует в сравнении | `test_simple_baseline`, конфиг `g_simple` | covered |
| FR-1.13 Строгая граница исполнителя | `executor/` (права, разовые операции, принадлежность; границы v1/v2r) | `test_executor`, `test_server_invariants_v2` | covered |
| FR-1.14 Проверка прав в базовой линии g0 | исполнитель вне перебираемых конфигураций, fingerprint границы | `test_configs` (FR-1.14), acceptance | covered |

## FR-1.15–1.19. Очередь, журнал, оценщик

| Требование | Покрытие | Проверка | Статус |
| --- | --- | --- | --- |
| FR-1.15 Очередь с ограниченной ёмкостью | `manual_queue` (дискретно-событийная модель) | `test_manual_queue`, probe `manual_queue` | covered |
| FR-1.16 Эскалации в общую очередь → задержка легального потока | парные прогоны, нагрузочный шаг в verify, ΔQ/L* | probe `queue_impact`, стресс-шаг `e2e_stress_*`, acceptance FR-1.16 | covered |
| FR-1.17 Единый журнал с затратами по компонентам | `event_log` (агент, гвардрейлы, исполнитель, решения) | `test_event_log`, acceptance | covered |
| FR-1.18 Независимый оценщик по состоянию БД | `evaluator` (дифф состояний, без текста агента) | `test_evaluator`, probe `evaluator` | covered |
| FR-1.19 Исходы executed / refused / ignored | классификация исходов в журнале оценщика; восприятие атакующего (`attackers/perception.py`) отдельно от оценщика засчитывает короткие подтверждения «Refund approved/created/issued …» и «Refund ID: …», фразы с отказом — нет (PR #25, `openspec/changes/attacker-live-adaptation/`) | `test_evaluator` (`dev_smoke`), `test_attackers`, `scripts/mutate_attackers.py` (21/21) | covered |

## FR-1.20–1.22. Атаки и корпус

| Требование | Покрытие | Проверка | Статус |
| --- | --- | --- | --- |
| FR-1.20 Режимы D и A | D — корпус, A — LLM-агент с лимитом попыток (bwrap-песочница); в режиме A после неудачной попытки — открытие другой тактикой, повторы помечаются `repeated_opener` / `repeated_message` (PR #25) | `test_attackers`, `test_rev10_broker_*`, `scripts/mutate_attackers.py` | covered |
| FR-1.21 Оси таксономии (механизм/цель/доставка) | разметка корпуса + паспорта `attack-cards.json` | `test_corpus`, `validate_attack_cards` | covered |
| FR-1.22 Чистые контроли для каждой атаки | `ctl-*` записи корпуса, сопоставимые по объёму | `test_corpus`, acceptance (FP-метрики) | covered |

## FR-1.23–1.24. Метрики и L*

| Требование | Покрытие | Проверка | Статус |
| --- | --- | --- | --- |
| FR-1.23 Автоматические метрики: безопасность, полезность, непрерывность, детекция, экономика | `metrics/` (все пять групп) | `test_metrics`, acceptance, таблица в `report_cli` | covered |
| FR-1.24 Порог окупаемости L* | `metrics/breakeven` + приёмка §9.2 (статусы по правилам, не по знаку) | `test_rev10_v2r`, acceptance FR-1.24 | covered |

## NFR-2.1–2.14. Воспроизводимость, стек, дизайн

| Требование | Покрытие | Проверка | Статус |
| --- | --- | --- | --- |
| NFR-2.1 Снимки и восстановление перед эпизодом | `state_store` (снимки, восстановление) | `test_state_store` | covered |
| NFR-2.2 Фиксированные seed, версии моделей, хеши корпуса | seeds в планах, хеш корпуса (`corpus verify`), версии в манифесте | шаги `configs_frozen`, `corpus_hash` в `verify.py` | covered |
| NFR-2.3 Расхождение траектории = дефект воспроизводимости | `repro` (хеши, replay) | `test_repro`, мутации confirm, acceptance | covered |
| NFR-2.4 Только OSS/self-hosted | адаптеры OSS-детекторов (выключены по умолчанию), закрытых API нет | `test_guard_plugins`, `oss_resource_check` | covered |
| NFR-2.5 Локальный контур, фиксация версий | socket ban (`contour.json`), `configs.lock.json`, OSS-локи | `report_cli verify` (contour), `configs_frozen` | covered |
| NFR-2.6 Модели ≤8B | **не подключено**: живая модель через `RESEARCH_LLM_*` не выбрана/не прогнана. Решение команды 2026-11-12: планка расширена до ≤16B (отклонение от рамки — обновить канон в репо `dvarp`) | — | **gap** → issue «модели и пилот live» (#6) |
| NFR-2.7 Языковой scope: dirty English, доки — русский | корпус английский, доки русские | `test_corpus` | covered |
| NFR-2.8 Парные прогоны | runner: attacked/base с общим seed и снапшотом | `test_runner`, acceptance (pair checks) | covered |
| NFR-2.9 Раздельный учёт затрат (self-limited / defender-borne) | отдельные счётчики атакующего; классификация вреда | acceptance, `test_metrics` | covered |
| NFR-2.10 Заморозка защиты при адаптивной атаке | `configs.lock.json` + FreezeGuard | `test_configs`, шаг `configs_frozen` | covered |
| NFR-2.11 Жёсткие лимиты атакующего, превышение = нарушение протокола | budget cutoff ≠ protocol violation | `test_runner`, `test_rev10_broker_record` | covered |
| NFR-2.12 Базовые линии g0 и g_proc | конфигурации в замороженном наборе | `test_configs` (leave-one-out) | covered |
| NFR-2.13 Синтетичность (нет PII/платежей/внешних интеграций) | локальные процессы, синтетический pay-emu | прогон под socket ban, review контура | covered |
| NFR-2.14 Артефакты: код, корпус, журналы, отчёт с Pareto/L*, статья | отчёт `report_cli` (Pareto, L*-таблица, неэффективные) + журналы | шаг `e2e_offline` + `e2e_verify` | covered (статья — в репо `dvarp`, вне полигона) |

## Канон исследования (design.md §10)

| Позиция канона | Статус | Примечание |
| --- | --- | --- |
| Один процесс возвратов, 5 инструментов | covered | `state_store`, `tools` |
| 3 модели-агента | **gap** | в каркасе 1 модель; выбор моделей ≤16B и пилот — issue #6 |
| 3 политики реакции | covered | `guard_policy` (continue/isolate/clarify/escalate/terminate), подмножества в конфигах |
| ≤16B (решение команды 2026-11-12, было ≤8B в рамке) | **gap** | вместе с моделями (#6) |
| Сначала one-shot, затем короткие диалоги | covered | лимит реплик канала, конфигурации |

## Factory-extra (решения)

| Компонент | Статус | Решение |
| --- | --- | --- |
| Оператор → выплата → статус (`shop_svc`, `pay_emu`, `svckit`, payout) | factory-extra | **оставить/деферить**: реализм завершения кейса; не в scope рамки; пересмотр после заполнения гэпов |
| 19 замороженных конфигураций фабрики (карта — `docs/configs.md`) | factory-extra | перезаморозка под исследовательскую матрицу §4 — issue |
| openspec change-каталоги фабрики (rev9–11) | factory-extra | оставить как историю/образец формата |
| Изоляция атакующего (broker, bwrap) | covered-heavy | нужна для честности атаки (критик rev9–11); не упрощать |
| Веб-консоль `console/` (Next.js, PR #26, `openspec/changes/web-console/`) | factory-extra | **консоль — обёртка над CLI из белого списка, монолит не трогаем**: запуск путей полигона, чтение `runs/` и `configs/`; слушает только 127.0.0.1, запросы к модели — только на loopback (NFR-2.5); проверка — `npm run lint`, `npm run typecheck`, `npm run build` |

## Гэпы → следующие issue

| Гэп | Issue | Предполагаемый исполнитель |
| --- | --- | --- |
| Модели ≤16B: выбор, подключение 3 агентов, пилот live | #6 | geekiot-hub (инфраструктура) |
| Конфиг-матрица §4: перезаморозка набора под канон | «конфиг-матрица §4» | axstiz |
| Кейс-паки: 6 сценариев цикла 9 → корпус + паспорта | «кейс-паки из спеки цикла 9» | BruhWhyMeHaveNoIdea (общий вид полигона) |
| Гейты отчёта: G-tau (ложные прерывания ≤5%), honest accounting | #9 | sser1to (требования) |
