# darp-revision-10 — progress

Стадия: integrate (deep_case), change `trusted-trace-and-v2r-table`, 5 одобренных задач.
Живых запросов: 0 (бюджет — artifacts/revision-10-budget.json).

## Сделано
- [x] Прочитаны brief, handoff, workflow-contract, improvement_darp_revision_10 (critique, plan, specs).
- [x] Снимок rev9-артефактов: artifacts/history_rev9/; revision-9-{plan,results,budget} — копии.
- [x] artifacts/revision-10-budget.json создан в начале прохода.
- [x] RED: artifacts/rev10/red/flow_comment_closed_checks.log (rev7/rev8 FAIL от комментария),
      hostile_child_before.json (подделки приняты / исключения runner), mx_mutants_before.log (MX2/3/4/6 выжили).
- [x] revision-scoped-checks (код): scripts/historical.py + artifacts/rev10/historical_artifacts.json(.sha256),
      verify_e2e revision3/5/7/8 → исторический контракт.
- [x] broker-authoritative-record: IPC v2 (ipc.py, sandbox.py, child), tests/hostile_util.py + tests/hostile/,
      tests/test_rev10_broker_record.py — 7 режимов порчи → ipc_protocol_error (GREEN). isolation_verify:
      D v1 seeds 7/8/9 и A stub побайтно идентичны.
- [x] broker-side-isolation-evidence: darp_range/isolation/broker_probes.py + probe_entry/probe_main.py,
      новые пробы pid_count/proc_environ_scan/cap_eff, host_* наблюдения; tests/test_rev10_broker_probes.py GREEN.

- [x] claim-level-in-manifest: manifest схема /2 (repro/manifest.py), Runner._preflight + claim,
      live_mode_a_v.py (bwrap по умолчанию, --in-process-research, manifest.json), матрица enforcement,
      defender_probe.py; tests/test_rev10_claim_matrix.py GREEN.
- [x] v2r-primary-table: verify_e2e (e2e_offline v2r, e2e_legacy_v1, legacy_v1_regression, primary_foundation),
      foundation в отчётах, приёмка FR-1.24 по правилам §9.2, FR-1.14 по основе; tests/test_rev10_v2r.py GREEN;
      приёмка на пробном v2r-прогоне 17/17 (tmp/v2r_probe, 921 с под нагрузкой).
- [x] scripts/mutate_rev10.py (19 мутантов), scripts/rev10_verify.py (без мутантов PASS 150 с после фикса маркера).
- [ ] эталон legacy v1 на коде rev9 (tmp/v1_ref → artifacts/rev10/legacy_v1_reference) — идёт.
- [ ] мутанты rev10 — идут; полный verify из чистой копии; документы.

## Сессия 2 (возобновление после обрыва)
- [x] Фоновые прогоны прошлой сессии оборваны (tmp/v1_ref пуст, tmp/mut10 — 15/19 без C10).
- [x] Ошибка в тестах: отрицательные контроли claim/matrix ловили побочную ошибку (отпечаток
      манифеста), из-за чего C10.2–C10.4 выживали; тест /1 пропускался в копиях. Исправлено в
      tests/test_rev10_claim_matrix.py (проверка конкретных сообщений). C10.1–C10.5 убиты 5/5
      (tmp/mut10_c/mutations.json).
- [x] forged_record FAIL в tmp/rev10_fast — устаревший лог до фикса маркера; повтор ok.
- [x] NFR-2.5 FAIL в tmp/v2r_probe: probe_entry/probe_main.py импортировал `probes` как
      модуль верхнего уровня → аудит импортов считал его сторонним. Исправлено (загрузка копии
      по явному пути), регрессия tests/test_rev10_contour.py.
- [x] эталон legacy v1 на коде rev9 (tmp/red_copy, 421 с) → artifacts/rev10/legacy_v1_reference (+README).
- [x] Найдено: побайтное равенство CSV недостижимо (затраты = измеренное CPU-время). Контроль —
      второй прогон кода rev9: те же 1928 ячеек затрат расходятся, стабильные столбцы равны
      (artifacts/rev10/legacy_v1_noise_control.json). Правило сравнения переделано: стабильные
      столбцы побайтно, VOLATILE — информационно; tests/test_rev10_legacy_compare.py; упрощение записано.
      Текущий код vs эталон: ok (artifacts/rev10/legacy_v1_current_vs_reference.json).
- [x] scripts/rev10_verify.py → artifacts/rev10/fast: PASS 131 с, мутанты 19/19 (MX3 эквивалентен).
- [x] simplifications.json: +6 записей rev10.
- [x] полный verify №1 из чистой копии: PASS, 3394 с, 108/108, ключевые 17/17
      (artifacts/rev10/clean_verify_run1*.{log,json}). Но рост +21.8 % к rev9 (2787 с) > 20 %.
      Ремонт без сокращения seeds: Steps.run_parallel — e2e_offline (v2r) и e2e_legacy_v1 одновременно.
- [x] verify №2: FAIL unit_tests — tests/test_rev10_v2r проверял текст `steps.run("e2e_legacy_v1"` (лексический
      тест не принял run_parallel). Тест исправлен (regex: legacy-шаг с --foundation v1). Логи —
      artifacts/rev10/clean_verify_run2_*.log. Урок: перед verify гонять полный isolated_unittest.
- [x] isolated_unittest: 1005 OK (artifacts/rev10/unittest_isolated.log); V10.1/V10.2 убиты; isolation_verify PASS 12/12.
- [x] полный verify №3 из чистой копии: PASS, 3079.6 с (+10.5 % к rev9), 108/108, 1005 OK, ключевые 17/17;
      diff -rq кода копии — 0. Доказательства: artifacts/checks/revision10_verify/.
- [x] status.json (scripts/rev10_status.py): 29 implemented / 9 simplified, verify_ok=true.
- [x] submission.json: notes rev10, change_check_command; tasks.md 5×[x]; openspec validate — valid.
- [x] rev10_verify на финальном коде: PASS 122 с (artifacts/rev10/fast, logs/rev10_verify_final.log).
- [x] report.md (§0 rev10, §3, §4–§8), improvement-results (раздел verify), evaluation-results (R10).
      unit 1005 OK/0 skip; e2e_offline v2r 408 с PASS; legacy v1 395 с PASS; legacy_v1_regression PASS;
      primary_foundation PASS. На заглушке v2r: V=0 у всех, формула no_safety_gain, статус unresolved.
- [x] документы: improvement-results (без раздела verify), openspec-results, coevolution-progress,
      case-update (§10), production-readiness (§0 rev10), components (repro/runner/report_cli/attackers/metrics).

- [x] scripts/rev10_status.py — дополнение status.json доказательствами rev10.

## Проверка
- sh scripts/isolated_verify.sh runs/verify   (из чистой копии, ~52 мин; verify_command)
- python3 scripts/rev10_verify.py --out runs/rev10_fast   (~2 мин, change_check_command)
- python3 scripts/isolation_verify.py --out runs/isolation_verify
- sh scripts/isolated_unittest.sh runs/unittest.log
- python3 scripts/historical.py check

## Итог стадии integrate rev10
Все 5 задач change implemented (оценка автора; финальную даёт критик). Живых запросов 0.

## Дальше (следующая стадия / критик)
Критик rev10: воспроизвести rev10_verify, мутанты, враждебного ребёнка, legacy-регрессию (правило VOLATILE),
verify из чистой копии. Остатки для следующего плана: изоляция защитника; sandbox по умолчанию в verify;
детерминированный учёт затрат офлайн; живой L* на v2r и слепой held-out критика.

## Стадия report rev10 (2026-11-10, handoff step=report, attempt 1/3)
- [x] Прочитаны brief.md, handoff.json (verification ok=true, checks=[], 2026-11-10 05:35:44 +05),
      workflow-contract.json, case/case.json, guardrails_requirements.md, guardrails-break-even.md целиком;
      methodology/README.md; прочие документы case/ — по структуре разделов (постановка принята, код их уже отражает).
- [x] Гигиена: символических ссылок 0, файлов-секретов по именам/сигнатурам 0 (.home/.cache — openspec/mcp-логи).
- [x] scripts/historical.py check → ok, failed=[], drift rev3/7/8 информационный
      (artifacts/checks/report_stage/historical_check.log).
- [x] scripts/rev10_verify.py → PASS 121.2 с, 16 проверок + мутанты (artifacts/checks/report_stage/rev10_verify.log).
- [x] status.json сверен с контрактом: 38/38 ID, доказательства существуют, simplifications = simplifications.json (253),
      decomposition 20 компонентов + components/*.md, out_of_scope пуст.
- [x] полный verify из чистой копии tmp/clean_verify_report10 (sh scripts/isolated_verify.sh runs/verify): PASS 3144.7 с,
      108/108, 1005 OK, ключевые 17/17 (первая попытка — exit 127 из-за отсутствия /usr/bin/time, не полигон).
      Копии доказательств — artifacts/checks/report_stage/.
- [x] report.md (заголовок, §3 итог report-стадии и handoff.verification, §4, §7, §8), status.json (last_verification,
      доказательства ключевых), submission.json (evidence revision10_report). Статусы требований не менялись: 29/9.

Дальше: независимая критика rev10 (см. report.md §7).
