# Ревизия 10 — результаты одобренных задач (change `trusted-trace-and-v2r-table`)

Фаза foundation, уровень A программы коэволюции (целостность трассы и доказательств изоляции) плюс
перенос главной таблицы L* на рекомендуемую основу v2r. Живых запросов к Qwen: **0**
(`artifacts/revision-10-budget.json`; общий `improvement-budget.json` не переписан и равен
`revision-9-budget.json`). Все проверки офлайн. Прежние результаты rev9 — `revision-9-results.md`,
снимок артефактов rev9 — `history_rev9/`. План — `improvement-plan.md`.

Проход шёл в двух сессиях: первая оборвалась на построении эталона legacy v1 и прогоне мутантов;
вторая (интеграция) нашла и исправила три дефекта собственных проверок (раздел «Найдено при
интеграции»).

## Сводка проверок

| проверка | команда | результат | доказательство |
|---|---|---|---|
| быстрая проверка change (16 проверок, мутанты) | `python3 scripts/rev10_verify.py --out artifacts/rev10/fast` | PASS, 122 с на финальном коде (цель ≤ 600 с; лог `artifacts/rev10/logs/rev10_verify_final.log`) | `artifacts/rev10/fast/summary.{json,md}` |
| мутанты rev10 | `python3 scripts/mutate_rev10.py` (внутри rev10_verify) | 18/19 убиты, MX3 — эквивалентный с доказательством CapEff | `artifacts/rev10/fast/mutations/mutations.json` |
| эталон legacy v1 на коде rev9 | `report_cli offline --seeds 7,8,9 --foundation v1` в копии rev9 | 25 таблиц, 421 с | `artifacts/rev10/legacy_v1_reference/` |
| контроль шума эталона | второй прогон кода rev9 против эталона | стабильные столбцы равны; 1928 ячеек затрат различаются | `artifacts/rev10/legacy_v1_noise_control.json` |
| legacy v1 текущего кода против эталона | `scripts/rev10_legacy_compare.py` | ok: 0 различий в стабильных столбцах | `artifacts/rev10/legacy_v1_current_vs_reference.json` |
| полный verify из чистой копии | `sh scripts/isolated_verify.sh runs/verify` в `tmp/clean_verify_rev10` | №3 PASS, 3079.6 с, 108/108, 1005 OK, ключевые 17/17 | `artifacts/checks/revision10_verify/` |

## Сопоставление задач

### revision-scoped-checks — implemented
- Код: `scripts/historical.py` (исторический контракт: sha256 предрегистрации + замороженный
  список записанных артефактов `artifacts/rev10/historical_artifacts.json`, его sha256 —
  `historical_artifacts.sha256`; расхождение кода — поле `drift`, не FAIL); `scripts/verify_e2e.py`
  — `revision3/5/7/8_checks` переведены на контракт, добавлены `revision9_checks` и шаг
  `closed_revisions`; пофайловый бюджет `artifacts/revision-10-budget.json`;
  быстрая команда `scripts/rev10_verify.py`.
- RED (до правки): `artifacts/rev10/red/flow_comment_closed_checks.log` — комментарий в
  `runner/flow.py` ронял `revision7_checks`/`revision8_checks` (`undeclared`).
- GREEN: `artifacts/rev10/fast/comment_change_green.json` — та же правка в полной копии:
  rev7/rev8 PASS, `flow.py` в `drift`.
- Отрицательные контроли: `tamper_controls.json` — байт предрегистрации rev8, порча сводки rev8,
  порча кассеты rev8 → FAIL хеш-проверки rev8 (rev7 PASS); `behaviour_controls.json` — мутант
  промпта агента ломает точный replay кассет rev7 runA (20 дефектов) и rev8 run1 (11), чистое
  дерево воспроизводит точно; мутанты H10.1 (drift снова FAIL), H10.2 (артефакты не проверяются)
  убиты.
- Закрытые ревизии без поведенческой проверки: **нет** (`closed_revisions.json`:
  `no_behavioural_check: []`, по каждой ревизии 1–9 перечислены поведенческие проверки).
- За ревизию 10 не добавлено ни одного файла отклонений (`preregistration-rev10-deviations.json`
  отсутствует; проверяется шагом бюджета).
- Остаток: drift кода по-прежнему виден (информационно) — это ожидаемо, не дефект.

### broker-authoritative-record — implemented
- Код: `darp_range/isolation/ipc.py` (IPC v2: `record` несёт только
  `self_report {tactics: [str], believed_success: bool}`), `darp_range/isolation/sandbox.py`
  (брокер строит `AttemptRecord` из своей копии канала: номер попытки из `started`,
  episode_id/brief_id брокера, реплики, ответы, status/violation; событие `attempt` ребёнка — только
  `cost=null`; любое исключение разбора/записи в `_pump`/`finish_attempt` → `IPC_VIOLATION`),
  ребёнок `isolation/child/attacker_child.py`.
- RED: `artifacts/rev10/red/hostile_child_before.json` (подделки приняты, исключения runner).
- GREEN: `artifacts/rev10/fast/hostile_child.json` — 7 видов порчи (forged_ids, forged_record,
  extra_turn, cost 1e9, foreign_episode, bad_self_report, tactics_mismatch) → у каждой
  `protocol_violation / ipc_protocol_error`, ipc_errors не пуст, ошибки эпизода нет, поддельные
  значения не найдены ни в записях, ни в журналах (`tests/hostile_util.py`, `tests/hostile/`).
- Регрессия: `regression_sandbox_vs_in_process.json` — побайтная идентичность записей,
  транскриптов и исходов оценщика (D: корпус v1, seeds 7/8/9; A: заглушка, 4 брифа × ≤3 попытки ×
  g0+g_in, seeds 7/8/9).
- Мутанты R10.1–R10.5 убиты.
- Остаток: `believed_success`/`tactics` — самоотчёт с меткой; смысл тактик не проверяется
  (simplifications.json).

### broker-side-isolation-evidence — implemented
- Код: `darp_range/isolation/broker_probes.py`, `darp_range/isolation/probe_entry/probe_main.py`
  (вне staged-пакета атакующего, монтируется в `/probe` тем же argv bwrap); новые пробы
  `pid_count`, `proc_environ_scan`, `cap_eff`; наблюдения хоста `host_argv_match`,
  `host_session_id`, `host_nspid_nested`, `host_die_with_parent`; `hello.self_check` — только
  информационное поле.
- GREEN: `artifacts/rev10/fast/probes/probes.md` — в sandbox все 23 отрицательные пробы и 4 наблюдения хоста DENY,
  позитивы PASS (видимых PID 2; CapEff=0; sid 1987 ≠ 1042; NSpid [1988, 2]; процесс исчез через
  0.05 с после SIGKILL родителя); in-process — L1 воспроизведена; слабый sandbox даёт утечки.
- Мутанты: MX2, MX4, MX6, B10.1 убиты; лживый launcher с чистым self_check не получает
  isolated-уровень. MX3 (без `--cap-drop ALL`) — эквивалентный: CapEff=0 с флагом и без
  (`mutations.json`, `mx3_evidence`), по документации bwrap cap-drop действует при привилегированном
  запуске.
- Остаток: `/nix/store` монтируется целиком (non-goal).

### claim-level-in-manifest — implemented
- Код: `darp_range/repro/manifest.py` (схема `darp.run_manifest/2`, `claim_level` + факты, проверка
  в `reproducibility_errors`), `darp_range/runner/orchestrator.py`, `scripts/live_mode_a_v.py`
  (живой A по умолчанию в bwrap, in-process — только `--in-process-research`),
  `configs/isolation/access_matrix.json` + `darp_range/isolation/matrix.py` (поле `enforcement`,
  собственная проба для каждой deny+os-строки), `darp_range/isolation/defender_probe.py`.
- GREEN (`tests/test_rev10_claim_matrix.py`): офлайн-прогон без фабрики → `in_process_research`;
  sandbox на корпусе v1 → `isolated_implementation_informed`; research-mode → не выше `research`;
  живой A без bwrap и без флага → код 2 при 0 запросах; проба защиты находит канарейку атакующего
  (`defender_gc_probe.json`, ожидаемый RED, записан в матрицу как known_leak строки
  defender→attacker_memory с `enforcement=convention`).
- Мутанты C10.1–C10.5 убиты (после исправления тестов, см. ниже).
- Прошлые манифесты /1 читаются, их fingerprint не пересчитывается.

### v2r-primary-table — implemented (с упрощением регрессии legacy)
- Код: `scripts/verify_e2e.py` (`e2e_offline` = `--foundation v2r`, seeds 7/8/9; `e2e_legacy_v1`
  + `e2e_legacy_v1_verify` + `legacy_v1_regression`; шаг `primary_foundation`), отчёты
  `report_cli` пишут строку `foundation=`, приёмка FR-1.24 (`report_cli/acceptance.py`) проверяет
  правила статусов §9.2, а не знак ΔV; FR-1.14 — граница из основы прогона.
- GREEN: `tests/test_rev10_v2r.py`; мутанты V10.1 (главный на v1) и V10.2 (ΔV=0 →
  `pays_at_threshold`) убиты.
- Регрессия legacy v1 (**упрощение**): побайтное равенство CSV недостижимо — затраты включают
  измеренное процессорное время; два прогона одного кода rev9 расходятся в 1928 ячейках
  денежных столбцов (`legacy_v1_noise_control.json`). Правило: заголовки, строки и все стабильные
  столбцы (статусы, режимы L*, счётчики, доли, ДИ ΔV) — побайтно, столбцы затрат — информационно;
  различие в столбце вне списка — FAIL (`tests/test_rev10_legacy_compare.py`, 7 тестов).
- Результат на заглушке v2r (пробный прогон и verify): все конфигурации — `unresolved` (ΔV в пределах
  шума seed); это отрицательный/неразрешённый результат, не гейт.

## Найдено при интеграции (исправления вне плана, необходимые)
1. **Отрицательные контроли claim/matrix ловили побочную ошибку.** Удалённый `claim_level` отвергался
   из-за несовпадения отпечатка манифеста, а не проверкой claim; порча матрицы — любым сообщением.
   Мутанты C10.2–C10.4 выживали (`tmp/mut10` первой сессии). Тесты теперь требуют конкретное
   сообщение правила; тест схемы /1 не пропускается в копиях без `artifacts/`. C10.1–C10.5 убиты.
2. **NFR-2.5 FAIL главного прогона v2r**: `probe_entry/probe_main.py` импортировал `probes` как модуль
   верхнего уровня — аудит импортов (`imports_stdlib_only`) считал его сторонней зависимостью.
   Исправлено загрузкой копии по явному пути; регрессия `tests/test_rev10_contour.py`.
3. **Побайтная регрессия legacy v1 неосуществима** (см. выше) — правило сравнения уточнено,
   упрощение записано, контроль шума сохранён.
4. **Рост verify +21.8 % > 20 %** — снят параллельным запуском независимых e2e (без сокращения seeds); лексический
   тест на текст verify_e2e переписан (verify №2).
Косметика: заголовок `probes.md` больше не говорит «ревизия 9».

## Полный verify
Все прогоны — `sh scripts/isolated_verify.sh runs/verify` в свежей чистой копии `tmp/clean_verify_rev10` (без `runs/`,
`tmp/`, `.cache`, `.home`, `.runtime`; netns без внешних интерфейсов, lo, непривилегированный uid, RESEARCH_LLM_* сняты).
- **№1**: PASS, 3394.1 с, 108/108 шагов, 1005 тестов OK (0 skip), ключевые 17/17. Но время +21.8 % к rev9 (2787.3 с) —
  выше порога 20 % задачи v2r-primary-table. Разбор по шагам: +395 с `e2e_legacy_v1`, +121 с `rev10_verify`, +37 с unit.
  Сокращение legacy до seed 7 ослабило бы проверки закрытых ревизий 1–5, построенные на v1 seeds 7/8/9; вместо этого
  независимые прогоны v2r и legacy v1 запускаются одновременно (`Steps.run_parallel`, те же argv, без сокращения).
  Логи: `artifacts/rev10/clean_verify_run1.log`, `clean_verify_run1_verify.json`, `clean_verify_run1_key_requirements.json`.
- **№2**: FAIL `unit_tests` — `tests/test_rev10_v2r.py` проверял текст `steps.run("e2e_legacy_v1"` и не принял
  параллельный запуск; тест переписан на проверку legacy-шага с `--foundation v1` (мутанты V10.1/V10.2 по-прежнему убиты).
  Прогон остановлен вручную после FAIL. Логи: `artifacts/rev10/clean_verify_run2_fail.log`, `clean_verify_run2_unit_tests.log`.
  Перед №3 — полный unittest в той же изоляции: 1005 OK (`artifacts/rev10/unittest_isolated.log`).
- **№3 (итоговый)**: **PASS**, exit 0, **3079.6 с (+10.5 % к rev9)**, 108/108 шагов, 1005 тестов OK, 0 skip, ключевые
  требования 17/17 PASS; `e2e_offline` и `e2e_legacy_v1` — 472 с одновременно (последовательно было 408 + 395 с;
  время второго шага — верхняя граница, simplifications.json); `legacy_v1_regression`, `primary_foundation`,
  `revision9_checks`, `closed_revisions`, `rev10_verify` PASS. Код копии совпадает с рабочим каталогом (`diff_rq.txt`).
  Доказательства: `artifacts/checks/revision10_verify/` (`clean_verify.log`, `verify.json`, `key_requirements.json`,
  `unit_tests.log`, `primary_foundation.json`, `legacy_v1_regression.json`, `closed_revisions.json`, `rev10/`, `e2e/`,
  `e2e_legacy_v1/`).
- `isolation_verify` (isolation_check_command rev9) на коде rev10: PASS 12/12, 85.7 с (`artifacts/rev10/logs/isolation_verify.log`).
- OpenSpec: `openspec validate trusted-trace-and-v2r-table --strict --no-interactive` — valid
  (`artifacts/rev10/logs/openspec-validate.log`); задачи tasks.md отмечены `[x]`.
- `status.json` пересобран `scripts/rev10_status.py`: 29 implemented, 9 simplified, verify_ok=true.

## Остатки (перенос в следующий план)
1. Изоляция защищаемой стороны (отдельный процесс, enforcement=os для defender→attacker_memory).
2. Sandbox по умолчанию в verify/offline (сейчас `in_process_research` с явной меткой).
3. Детерминированный учёт затрат офлайн для побайтной регрессии таблиц.
4. Живой L* на v2r, слепой held-out критика, живой срез A через sandbox — не начаты (0 запросов).
