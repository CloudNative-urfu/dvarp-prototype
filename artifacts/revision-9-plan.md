# Ревизия 9 — план одобренных улучшений (стадия integrate, 2026-11-09)

Вход: `brief.md` (5 одобренных задач, change `attacker-isolation-boundary`), `openspec/changes/attacker-isolation-boundary/`
(proposal, design, specs attacker-isolation и attack-provenance, tasks), `improvement_darp_revision_9/{proposal/*, review/*,
critique.md, case-update.md, coevolution.md}`. Фаза — **foundation**, уровень A программы coevolution.
Прежние план/результаты/бюджет rev8 сохранены: `artifacts/revision-8-{plan,results}.md`, `artifacts/revision-8-budget.json`,
снимок артефактов rev8 — `artifacts/history_rev8/`.

Живых запросов к Qwen в этом проходе **0** (все задачи офлайн, заглушка и фейковый транспорт). run_command прежний.

## Аудит до правок (факты, проверены в этой сессии)

- L1 воспроизведена: `EventLog("l1").writer("attacker")._emit.__self__` → `EventLog`, `_events` читаются
  (лог `artifacts/rev9/red/l1_inprocess.log` после реализации проб). Докстринг `event_log/log.py:6-9` ложен.
- `darp_range/attackers` импортирует `channel` (константы, Observation, SessionResult), `event_log` (ComponentWriter),
  `llm` (Message, ChatModel, LimitExceeded, make_chat_model, StubReply/StubRequest), `repro` (hash_obj, make_rng).
  Пакеты защиты (`executor/agent/evaluator/guard_*/state_store/corpus`) атакующий не импортирует (кроме `__main__`-демо).
- bubblewrap 0.12.0 в PATH (`/nix/store/...-bubblewrap-0.12.0/bin/bwrap`), Python 3.14.8 из /nix/store.
- Оркестратор создаёт атакующего в `runner/flow.py::_attack` через `make_attacker` и `run_campaign`.
- `configs/*.json` сканируется lock-проверкой (`freeze.verify`: неизвестный файл → drift «unlisted»), поэтому матрица
  кладётся в подкаталог `configs/isolation/`. `DEFENSE_CODE` не включает runner/attackers/event_log/evaluator — lock v6
  не перехешируется.

## Архитектура (решения до кода)

- Новый пакет `darp_range/isolation/`: `matrix.py` (загрузка и проверка матрицы), `ipc.py` (закрытая схема JSON-lines),
  `sandbox.py` (сборка staged-пакета, argv bwrap, слабый контроль, RemoteAttacker-брокер), `child/` (исходники процесса
  атакующего: main, шимы `channel/event_log/llm/repro`), `probes.py` (OS-пробы, выполняются внутри процесса атакующего),
  `audit.py` (журнал payload), `invariance.py` (дифференциальный тест и утечка промпта), `provenance.py` (реестр и
  claim_level).
- Staged-пакет: побайтные копии `attackers/{__init__,attacker,composers,perception,prompt,stub,tactics,types}.py`,
  `channel/types.py`, `llm/{types,limits}.py`, `repro/{hashing,seeds}.py` + шимы. Остальной `darp_range` в sandbox не
  монтируется (проба `import_defender_modules`). Хеши staged-файлов пишутся в манифест.
- Брокер (родитель) держит канал, ChatModel (заглушка или живая; ключ — только у него), EventLog, журнал payload.
  Ребёнок шлёт `event` (пишется настоящим writer("attacker") брокера), `llm_request`, `started`, `message`, `record`,
  `error`. Неизвестный тип/поле → `IPCProtocolError` → попытка = нарушение протокола `ipc_protocol_error`, не успех защиты.
- bwrap: `--unshare-all --die-with-parent --new-session --clearenv --cap-drop ALL`, `--ro-bind /nix/store`,
  `--ro-bind <stage> /attacker`, `--ro-bind <input-dir> /input`, `--tmpfs /tmp`, `--proc /proc`, `--dev /dev`,
  `--chdir /tmp`; сам bwrap запускается с пустым окружением (иначе ключ виден в `/proc/1/environ` монитора bwrap —
  проверяется пробой). Отказ bwrap → `SandboxUnavailable`; с `fallback="in_process_research"` прогон идёт in-process
  с этой меткой.
- Интеграция: `FlowContext.attacker_factory` / `Runner(attacker_factory=...)` (по умолчанию `None` → прежний
  `make_attacker`, поведение и хеши не меняются; in-process помечается `in_process_research` в отчётах).

## Задачи и критерии (из approved specs; не ослабляются)

| ID | Изменение | Критерий приёмки | RED / отрицательные контроли | Ресурсы |
|---|---|---|---|---|
| access-matrix | `configs/isolation/access_matrix.json` + `darp_range/isolation/matrix.py`; докстринг `event_log/log.py` | Тест: каждая deny-строка attacker ссылается на существующую пробу, каждая allow — на позитивный тест; ключ только broker; boundary-2r current, boundary-1 legacy | Удаление ссылки на пробу / новая deny-строка без теста / ключ у attacker → тест матрицы падает (мутации в тесте и в `scripts/mutate_rev9.py`) | офлайн |
| attacker-sandbox-ipc | bwrap-ребёнок + брокер + IPC; `attacker_factory` в runner | Побайтно равные реплики, транскрипты, AttemptRecord и исходы оценщика: D на корпусе v1 и A stub (≥3 брифа × 3 попытки), seeds 7/8/9, sandbox vs in-process; `RESEARCH_LLM_*` и канарейки нет в окружении ребёнка; отказ bwrap → ошибка или `in_process_research`; неизвестное поле IPC → protocol error | Подменённый путь bwrap (несуществующий, `/bin/false`); ребёнок с лишним полем; время A stub публикуется (цель ≤ +20 %, превышение фиксируется) | офлайн |
| os-negative-probes | `darp_range/isolation/probes.py`, `scripts/isolation_probes.py`, `tests/test_isolation_probes.py` | ≥14 отрицательных проб, все DENY в sandbox; in-process gc/EventLog-проба PASS (L1); слабый sandbox: ≥1 файловая и `/proc/<ppid>/environ` PASS; позитивные контроли PASS; ≤5 мин | Слабый sandbox и in-process — контроли чувствительности; мутант «bwrap без --unshare-net / с bind корня проекта» → проба PASS | офлайн |
| payload-audit-invariance | `audit.py`, `invariance.py`, исход `system_prompt_leak_via_reply` | 4 скрытых изменения × ≥3 брифа: sha256 всех payload равны; изменение одного ответа меняет следующий payload; цитирование канарейки → канарейка в payload и leak=true, без цитирования false и канареек нет; ключ-канарейка не в журнале | Изменённый ответ агента (обязан менять хеш); мутант «брокер добавляет системный промпт агента в payload» → тест красный | офлайн |
| attack-provenance-gate | `configs/isolation/provenance_registry.json`, `provenance.py`, поле `provenance` у брифа (вне `to_json`, MANIFEST не меняется) | Корпуса v1/holdout_v2/heldout_v3/trusted_v7 → implementation-informed; sandbox+legacy → `isolated_implementation_informed`; in-process → `in_process_research`; research-mode → никогда black-box; провал пробы → `in_process_research` с записью `sandboxed=true`; поддельный blind-critic → отказ | Поддельный бриф, research-mode, проваленная проба | офлайн |

## Порядок и гейты

1. RED: L1 (gc/EventLog) на текущем коде; слабый sandbox читает файлы и `/proc/<ppid>/environ`.
2. access-matrix + probes → sandbox → IPC/брокер → регрессия D/A → audit/invariance → provenance/claim_level.
3. GREEN: `scripts/isolation_verify.py` (отдельная быстрая команда ≤5 мин, все проверки change) + новые unittest.
4. Мутации `scripts/mutate_rev9.py` (каждая обязана дать RED).
5. Регрессия: полный unittest (исходы прежних тестов без изменений) и полный `verify_command` из чистой копии под
   `scripts/isolated_verify.sh` (50 мин, не расширяется шагами rev9, кроме одного шага-ссылки на быструю команду — нет,
   см. ниже).
6. Отчёты: `improvement-results.md`, `openspec-results.json`, `coevolution-progress.md`, `case-update.md`, `report.md`,
   `status.json`, `submission.json`, `simplifications.json`, `production-readiness.md`, `attack-cards.json` (без новых атак).

Решение по verify: по плану/ревью новые проверки — отдельная команда (`submission.json:isolation_check_command`),
verify_command не растёт. Неизвестное: на хостах без user namespaces sandbox-проверки дают явный SKIP/ошибку
с меткой `in_process_research`, а не PASS.

## Бюджет

0 живых запросов автора; весь резерв прохода (180 запросов/3600 с, критику 60/900) не тратится автором —
`artifacts/improvement-budget.json`.
