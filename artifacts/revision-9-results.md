# Ревизия 9 — результаты одобренных задач (change `attacker-isolation-boundary`, 2026-11-09)

Фаза foundation, уровень A программы coevolution. Живых запросов к Qwen: **0** (`improvement-budget.json`).
Все проверки офлайн. Прежние результаты rev8: `revision-8-results.md`, снимок артефактов `history_rev8/`.

## Сводка проверок

| проверка | команда | результат | доказательство |
|---|---|---|---|
| быстрая проверка change (12 проверок) | `python3 scripts/isolation_verify.py --out artifacts/rev9/isolation` | PASS, ≈70 с | `artifacts/rev9/isolation/summary.{json,md}` |
| OS-пробы, 3 режима | `python3 scripts/isolation_probes.py` | PASS, 0.3 с | `artifacts/rev9/isolation/probes/probes.{json,md}` |
| мутации rev9 | `python3 scripts/mutate_rev9.py` | 12/12 убиты, чистая копия зелёная | `artifacts/rev9/mutations/mutations.json` |
| полный unittest в изоляции (netns+lo+uid) | `sh scripts/isolated_unittest.sh artifacts/rev9/unittest_after_isolated.log` | 970 тестов OK (935 прежних + 35 новых), 203 с | `artifacts/rev9/unittest_after_isolated.log` |
| полный verify_command из чистой копии | `sh scripts/isolated_verify.sh runs/verify` в `tmp/clean_verify_rev9` | см. раздел «Полный verify» | `artifacts/rev9/clean_verify.log` |
| OpenSpec | `openspec validate attacker-isolation-boundary --strict --no-interactive` | valid | `artifacts/rev9/openspec-validate.log` |

## Сопоставление задач

### access-matrix — implemented
- Код: `configs/isolation/access_matrix.json` (38 строк, модель угроз, `boundary.current=boundary-2r`, `legacy=[boundary-1]`),
  `darp_range/isolation/matrix.py`; исправлен докстринг `darp_range/event_log/log.py` (было: «атакующий код, получивший
  писателя, не может прочесть логи» — ложно; стало: это не граница, граница — отдельный процесс).
- Тест: `tests/test_isolation_matrix.py` (10 тестов): валидность; каждая deny-строка атакующего → существующая проба
  (20 строк, покрыты все 19 проб); удаление ссылки / deny без теста / allow без позитивного теста / ключ не у broker → FAIL;
  статический тест: код защиты и оценщика не читает RESEARCH_LLM_*/LLMSettings (фабрики моделей брокера —
  `agent/factory.py`, `attackers/attacker.py::make_attacker` — исключены явно).
- Остаток: строки defender/evaluator → `llm_api_key: deny` держатся на соглашении + статическом тесте (общий процесс с
  брокером); это записано в матрице (`note`) и в simplifications.

### attacker-sandbox-ipc — implemented
- Код: `darp_range/isolation/{sandbox,ipc,regression}.py`, `darp_range/isolation/child/` (процесс атакующего и шимы),
  `runner/flow.py` (`FlowContext.attacker_factory`, закрытие процесса после кампании), `runner/orchestrator.py`
  (`Runner(attacker_factory=...)`). По умолчанию поведение runner не меняется (unittest 935 прежних — OK).
- RED до правок: L1 — `writer("attacker")._emit.__self__` → `EventLog` (проба `gc_eventlog_canary` PASS на in-process).
- Регрессия (`artifacts/rev9/isolation/regression_mode_{d,a}.json`): сравниваются канонические кампании
  (`AttemptRecord`, реплики, тактики), транскрипты канала, траектории эпизодов (включая события атакующего, без
  wall-clock), итоговые хеши БД, вердикты оценщика.
  - D, корпус v1 (24 атаки), g0, seeds 7/8/9 (с повтором первого блока): 216 эпизодов на seed — **идентичны**.
  - A, заглушка, брифы atk-01/13/17/21, g0+g_in, до 3 попыток, seeds 7/8/9: 34 эпизода, 18 попыток, ≥3 кампании с 3
    попытками на seed — **идентичны**; payload-запросов 43 на seed.
- Ключ: процесс атакующего без RESEARCH_LLM_* (самопроверка `hello.self_check`, проба `env_secret_self`; тест с
  синтетическим ключом в окружении брокера).
- Отказ sandbox: несуществующий bwrap и bwrap, завершающийся с кодом 1 → `SandboxUnavailable` (Runner без отката
  останавливается); с `fallback="in_process_research"` прогон идёт in-process и `claim_level=in_process_research`.
- Неизвестное поле IPC (`{"t":"message","text":"hi","stolen":"x"}`) → попытка `protocol_violation/ipc_protocol_error`,
  `counts_as_result=false`, кампания остановлена.
- Время (`overhead_mode_a.json`): режим A, заглушка, все 24 атаки v1, g0: медиана 1.575 с in-process vs 1.719 с sandbox,
  **+9.1 %** (цель ≤ +20 %). Первая версия давала +55…69 % (процесс на кампанию, ~60 мс); исправлено запасным процессом
  (`begin`) и фоновым завершением. Режим D (полный корпус, g0): +7.5…13 %.
- Остаток: verify_command и исторические скрипты — in-process (Migration Plan); живой Qwen через sandbox не запускался.

### os-negative-probes — implemented
- Код: `darp_range/isolation/{probes,probe_world,probe_run}.py`, `scripts/isolation_probes.py`, `tests/test_isolation_probes.py`.
- 19 отрицательных проб (исходники защиты, БД эпизода, events.jsonl, кассеты, GT корпуса и реестр происхождения, case/,
  критика/артефакты, runs/, symlink из attack-only каталога, `../` traversal, `/proc/<ppid|broker|1>/environ`,
  `/proc/<…>/fd`, unix-сокет брокера (путь и abstract), TCP 127.0.0.1 брокера, внешняя сеть (интерфейсы/маршруты),
  запись вне tmpfs, поиск `EventLog` через gc, собственное окружение, импорт пакетов защиты) + 3 позитивных.
- Sandbox: 19/19 DENY, 3/3 PASS, канареек нет. In-process: 18 утечек, `gc_eventlog_canary` PASS с канарейкой (L1).
  Слабый sandbox: 16 утечек, в т. ч. файловые с канарейками и `/proc/<ppid>/environ` с канарейкой.
- Пробы нашли 2 дефекта первой версии sandbox (`artifacts/rev9/red/probes_v0_before_stderr_remount_fix.json`):
  stderr процесса был файлом хоста → его путь и содержимое видны через `/proc/1/fd/2`; корень sandbox был доступен на
  запись. Исправлено (stderr — pipe, `--remount-ro /`), после — `probes_v1_after_fix.json`.
- Чувствительность: `--bind <корень проекта>` → 11 утечек; `--share-net` → 3; мутанты M9.1 (bwrap с окружением брокера:
  ключ виден в `/proc/1/environ`), M9.2 (без `--unshare-all`), M9.3 (агент в staged-пакете), M9.4 (корень rw) — убиты.

### payload-audit-invariance — implemented
- Код: `darp_range/isolation/{audit,invariance}.py`, `darp_range/evaluator/prompt_leak.py`.
- 3 брифа × 3 попытки × 7 вариантов (`artifacts/rev9/isolation/invariance.json`): R5, GT, системный промпт, политика
  гвардрейла → sha256 всех 63 payload равны базовым; изменённый первый ответ → первый отличающийся payload — запрос №1
  (сразу после ответа); цитирование канарейки промпта → канарейка в payload, `system_prompt_leak_via_reply=true`;
  без цитирования — false, и ни одна скрытая канарейка (промпт, политика) не появилась ни в одном payload; ключ-канарейка
  отсутствует в журнале payload.
- Фейковый живой транспорт (`tests/test_isolation_audit.py`): ключ уходит только в заголовок фейкового сервера;
  payload журнала = ровно `messages` тела запроса; URL и Authorization в журнале отсутствуют.
- Остаток: ответы агента фиксированы — это регрессионный страж барьера (ревью Р2), не измерение утечек живой моделью.

### attack-provenance-gate — implemented
- Код: `configs/isolation/provenance_registry.json` (воспроизводим `provenance.build_registry()`), `darp_range/isolation/provenance.py`.
- 60 атак 4 корпусов → implementation-informed; sha256 MANIFEST записаны и совпадают с preregistration-rev3 для v1,
  holdout_v2, heldout_v3; тексты корпусов не менялись.
- `claim_level` (`artifacts/rev9/isolation/summary.json`, `claim_sandbox_legacy.json`): sandbox+пробы+v1 →
  `isolated_implementation_informed`; research-mode с зарегистрированными слепыми брифами (синтетический реестр) →
  `isolated_implementation_informed`; тот же синтетический набор без research → `isolated_black_box_candidate`
  (положительный контроль правила, не заявление); поддельный `blind-critic` → `ProvenanceError`; провал пробы →
  `in_process_research` с `sandboxed=true`; in-process → `in_process_research`.

## Изменения вне плана (обоснование)
- `runner/flow.py`, `runner/orchestrator.py`: точка подключения фабрики атакующего — необходимая зависимость
  attacker-sandbox-ipc; значение по умолчанию сохраняет прежнее поведение (unittest без изменений исходов).
- `darp_range/evaluator/prompt_leak.py`: новый модуль исхода `system_prompt_leak_via_reply`; существующий `evaluate`
  не менялся (вердикты и отпечатки прежних прогонов не меняются).
- `scripts/isolated_unittest.sh`: запуск полного unittest в той же изоляции, что verify (найденная ошибка: импорт-аудит
  NFR-2.5 видел `darp_ipc`/`darp_probes` как сторонние модули — исправлено переносом в staged `darp_range/isolation/`).
- **D9.1** (`artifacts/preregistration-rev9-deviations.json`): `darp_range/runner/flow.py` заморожен предрегистрациями
  rev7/rev8; первый verify из чистой копии упал на `revision7_checks` (необъявленное изменение — хук фабрики). Отклонение
  объявлено после правки и до финального verify; `scripts/verify_e2e.py` читает его так же, как отклонения rev8
  (образец D8.2–D8.5). Исходный FAIL сохранён: `artifacts/rev9/clean_verify_run1_fail.log`.
- Lock конфигураций v6 не менялся: `DEFENSE_CODE` не включает изменённые пакеты (`configs.verify` без drift).

## Полный verify
- №1 (чистая копия): 116 PASS, 2 FAIL (`revision7_checks`, `revision8_checks`: необъявленное изменение `runner/flow.py`)
  → D9.1. Лог: `artifacts/rev9/clean_verify_run1_fail.log`.
- №2: 117 PASS, 1 FAIL `revision8_checks` — `KeyError: 'requests_total'`: проверка бюджета rev8 читала
  `artifacts/improvement-budget.json`, который в ревизии 9 содержит бюджет текущего прохода. Ремонт **D9.2**: проверка читает
  архивную копию `artifacts/revision-8-budget.json` (как D8.6 для rev7); пороги не менялись. Лог: `artifacts/rev9/clean_verify_run2_fail.log`.
- **№3 (итоговый)**: `sh scripts/isolated_verify.sh runs/verify` в свежей чистой копии `tmp/clean_verify_rev9` — **PASS**, exit 0,
  2787.3 с; шаги 118/118 PASS; unittest 970 OK, 0 skip (тесты изоляции выполнены под bwrap внутри netns);
  ключевые требования 17/17 PASS; `darp_range/ scripts/ tests/ configs/ corpus/` копии совпадают с рабочим каталогом (`diff -rq`).
  Доказательства: `artifacts/checks/revision9_verify/` (`clean_verify3.log`, `verify.json`, `key_requirements.json`,
  `revision7_checks.json`, `revision8_checks.json`, `unit_tests.log`, `environment.json`, `diff_rq.txt`).
- Финальный `isolation_verify`: PASS 12/12, 66.7 с (`artifacts/rev9/isolation_verify_final.log`, `artifacts/rev9/isolation/summary.json`).

## Остатки (перенос в следующий план)
1. Симметричная изоляция защищаемой стороны (отдельный процесс, тот же брокер и пробы).
2. Sandbox — путь по умолчанию для новых прогонов коэволюции и живого A; живой срез A через sandbox (бюджет).
3. Уровень B (раздельные хранилища опыта) и C (лига) не начаты; blind held-out отсутствует → ни один прогон не
   получает `isolated_black_box_candidate` на реальных данных.
4. critique rev8 п.1, п.2, п.5 (v2r в главной таблице, живой L*, быстрый verify) остаются открытыми (ревью Р4).
5. `/nix/store` целиком в sandbox; claim_level не в manifest.json.
