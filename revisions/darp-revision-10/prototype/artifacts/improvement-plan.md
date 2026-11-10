# Ревизия 10 — план одобренных улучшений (стадия integrate, 2026-11-09)

Вход: `brief.md` (5 одобренных задач, change `trusted-trace-and-v2r-table`),
`openspec/changes/trusted-trace-and-v2r-table/` (proposal, design, 5 specs, tasks),
`improvement_darp_revision_10/{critique.md, case-update.md, proposal/*, review/*}`.
Фаза — **foundation** (уровень A программы coevolution: целостность трассы и доказательств
изоляции), плюс перенос главной таблицы `L*` на рекомендуемую основу v2r.
Прежние план/результаты/бюджет rev9 сохранены: `artifacts/revision-9-{plan,results,budget}.*`,
снимок артефактов rev9 — `artifacts/history_rev9/`.

Живых запросов к Qwen в этом проходе по плану **0** (все задачи офлайн: заглушка и фейковый
транспорт). Учёт — `artifacts/revision-10-budget.json` (создан в начале прохода).

## Аудит до правок (факты этой сессии)

- Правка одного комментария `darp_range/runner/flow.py` в копии роняет первую проверку
  `revision7_checks` и `revision8_checks` (`undeclared: ["darp_range/runner/flow.py"]`) —
  `artifacts/rev10/red/flow_comment_closed_checks.log`.
- Враждебный процесс атакующего под настоящим argv bwrap (`tests/hostile/hostile_child.py`, на
  коде rev9): поддельный record с лишней репликой и `believed_success=true` принят,
  `cost.tokens_in=1e9` принят, чужой `episode_id` → `EventLogError` прогона, неверная форма
  record → `TypeError` прогона, поддельный `episode_id` → `KeyError` прогона, пропуск хода
  принят — `artifacts/rev10/red/hostile_child_before.json`.
- Мутанты критика MX2 (без `--die-with-parent`), MX3 (без `--cap-drop ALL`), MX4 (без
  `--new-session`), MX6 (без pid/ipc namespace) выживают на тестах `test_isolation_*` rev9 —
  `artifacts/rev10/red/mx_mutants_before.log`.
- `manifest.json` прогона verify (схема `darp.run_manifest/1`) не содержит `claim_level`;
  матрица без поля `enforcement` проходит валидацию; строка defender→attacker_memory — `deny`.
- `report_cli --foundation` по умолчанию `v1`; `e2e_offline` в verify — v1.
- Полный verify rev9 — 2787 с (артефакт `artifacts/checks/revision9_verify/verify.json`), из
  них `e2e_offline` (v1) 383 с.

## Задачи, критерии, RED → GREEN

| ID | Что меняется | GREEN-критерий (из spec) | Отрицательные контроли / мутанты |
|---|---|---|---|
| revision-scoped-checks | `scripts/historical.py` + замороженный список записанных артефактов `artifacts/rev10/historical_artifacts.json` (+ `.sha256`), создан до правок кода; `revision3/5/7/8_checks` → исторический контракт; `revision9_checks`, шаг `closed_revisions`; пофайловые бюджеты; `scripts/rev10_verify.py` | правка комментария flow.py не роняет проверки закрытых ревизий и видна в `drift`; 0 новых `file_hash_updates`; rev9 читает только `revision-9-budget.json` | байт в `preregistration-rev8.json`, порча сводки/кассеты rev8 → FAIL; мутант промпта агента ломает точный replay rev7 runA/rev8 run1; мутанты H10.1/H10.2 |
| broker-authoritative-record | IPC v2: `record` несёт только `self_report {tactics, believed_success}`; брокер строит `AttemptRecord` из своей копии канала; события ребёнка — только своей попытки/эпизода, `attempt` с `cost=null`, `t_sim=null`; любая ошибка разбора/записи → `ipc_protocol_error` | 6 (+1) видов порчи → `protocol_violation/ipc_protocol_error`, без ошибки эпизода и без поддельных значений в записях/журнале; побайтная регрессия D v1 seeds 7/8/9 и A stub ≥3×3 | мутанты R10.1–R10.5 (номер попытки от ребёнка, без сверки числа тактик, cost принят, ошибки событий не перехвачены, self_report без проверки формы) |
| broker-side-isolation-evidence | `isolation/broker_probes.py` + `probe_entry/probe_main.py` (вне staged-пакета, тот же argv bwrap); пробы `pid_count`, `proc_environ_scan`, `cap_eff`; наблюдения хоста sid/NSpid/argv/die-with-parent; `self_check` — только информация | все negative DENY в sandbox, позитивы PASS; слабый sandbox и in-process дают утечки; лживый launcher с чистым self_check не получает isolated-уровень | MX2/MX4/MX6/B10.1 убиты; MX3 — убит или эквивалентен с выводом CapEff с флагом и без |
| claim-level-in-manifest | схема `darp.run_manifest/2`: `claim_level` + факты в каждом manifest.json (runner/report_cli/`live_mode_a_v.py`); без фабрики — `in_process_research`; research-mode → не выше `research`; живой A по умолчанию в bwrap, in-process только `--in-process-research`; матрица: `enforcement`, своя проба на os-deny-строку, defender→attacker_memory = convention + известная утечка | манифест офлайн-прогона → `in_process_research`; sandbox на v1 → `isolated_implementation_informed`; живой A без bwrap и флага → ошибка, 0 запросов | удаление `claim_level` → `load_manifest` FAIL; deny+os без своей пробы → валидатор FAIL; мутанты C10.1–C10.5 |
| v2r-primary-table | `e2e_offline` = `--foundation v2r` seeds 7/8/9; `e2e_legacy_v1` (+ verify, + побайтная регрессия таблиц против кода rev9); `primary_foundation`; отчёты пишут `foundation=`; приёмка FR-1.24 проверяет статусы §9.2, а не знак ΔV; FR-1.14 — граница из основы прогона | `foundation=v2r` в главном отчёте, `foundation=v1 (legacy)` в legacy; приёмка 17/17 без требования ΔV>0 | мутанты V10.1 (главный на v1), V10.2 (ΔV=0 → `threshold`); подмена статуса в metrics.json → FAIL |

Порядок: revision-scoped-checks → broker-authoritative-record → broker-side-isolation-evidence →
claim-level-in-manifest → v2r-primary-table → `scripts/rev10_verify.py` → один полный verify из
чистой копии (`scripts/isolated_verify.sh`).

## Ресурсы и правила

- Офлайн, 0 запросов к моделям; bubblewrap 0.12.0 и user namespaces есть.
- Исторические артефакты и предрегистрации не редактируются; новые прогоны — в `artifacts/rev10/`.
- Время verify публикуется; при росте > 20 % legacy v1 сокращается до seed 7 с записью
  упрощения, v2r не сокращается.
- Ожидаемый на заглушке исход для v2r — `no_safety_gain`/`unresolved` (не гейт).
