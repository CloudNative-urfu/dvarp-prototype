# darp-revision-7 — progress (integrate, attempt 1)

## Выполнено
- План: `artifacts/improvement-plan.md`; rev6 план/результаты/бюджет → `artifacts/revision-6-*`,
  копии документов rev6 → `artifacts/history_rev6/`.
- T2 truncation-monotonic: `scripts/live_mode_a_v.py:label_rows` (truncated + executed → executed);
  тест `tests/test_revision7_truncation_monotonic.py` (RED на коде rev6 подтверждён в tmp/red: 3 FAIL);
  `scripts/rev7_relabel_live.py` — изменились 2 строки: rev3_run2 atk-07 g0, rev4_critic atk-05 g0
  (null→executed, обе truncated_episode_budget); measured не меняются.
- T1 review-routing (код): `executor/boundary.py` (executor-boundary-2r, отпечаток, карта
  BOUNDARY_V2_TO_V2R), `executor/executor.py` (`_route_review` в BEGIN IMMEDIATE, agent_view
  escalated, `exec_result.routed_by=server`).
- T5 authorizer-depth (код): `_deny_trusted_writes` расширен для 2/2r; тест ролей переименован.
- legal_suite: NonRecoveringAgent, CompliantAgent, SCENARIOS_V7/ATTACK_SCENARIOS_V7, 2r.
  RED/GREEN воспроизводится; rev6_foundation_table побайтно совпадает с artifacts/checks/revision6.

## Проверки
- `python3 -m unittest tests.test_revision7_truncation_monotonic tests.test_revision6_live_truncation` OK
- `python3 -m unittest tests.test_executor tests.test_server_invariants_v2 tests.test_foundation_v2 tests.test_tools tests.test_state_store` OK
- `python3 scripts/rev6_foundation_table.py --out tmp/ft6/foundation_table.json` == rev6 (cmp)

## Не завершено / следующий шаг
- runner/plan v2r + trusted_fixture declared + corpus trusted_v7; тесты 2r/авторизатора; мутации rev7;
  диагностика 2 vs 2r; перезаморозка lock (v5) + deviations; live slice; verify; документы.

## Блок 2 (после регрессии и живого среза)
- trusted-facts-declared: `corpus/trusted_v7` (scripts/make_trusted_v7.py, sha ef663b9f…), `fixture.trusted`
  в схеме/bind, `runner/flow.trusted_fixture(conn, bound)` (только объявленное), исторический
  `trusted_fixture_gt_rev6` по `RunPlan.trusted_facts`; `--foundation v2r`, `--trusted-facts`.
- lock конфигураций v5 (только defense_code.executor), v4 → configs/history/configs.lock.005…
- Тесты: tests/test_review_routing_v2r.py (26), tests/test_revision7_foundation.py (11),
  tests/test_revision7_truncation_monotonic.py (8) — OK; scripts/mutate_rev7.py 16/16 убиты.
- Регрессия rev6_boundary_diag: JSON без seconds и md совпали с artifacts/checks/revision6 и revision6_verify.
- Предрегистрация `artifacts/preregistration-rev7.json` (sha a54f091f…) до диагностики и живых запросов.
- Живой срез: runA 52 запроса/111383 ток., runB 26/55643; replay по кассетам точный;
  сводка `artifacts/checks/revision7/live_legal/summary.{json,md}`: вердикт «эффект не наблюдался»
  (0 измеренных (b) на boundary-2; 5 эпизодов (b) обрезаны лимитом 5 запросов → not_measured 5/18).
- Паспорта: attack-cards-2 (129 карточек), валидатор OK.
## Дальше
- дождаться rev7_boundary_diag; deviations rev7 (D7.1 токены, D7.2 ticket_id пары, D7.3 lock v5);
  verify_e2e шаги rev7; production-readiness/case-update/report/status/submission/simplifications;
  полный verify из чистой копии.

## Блок 3 (integrate, завершение; новая сессия 2026-11-07 ~23:20 +05)
- Установлено: полный verify из чистой копии tmp/clean_verify (старт 14:06:15Z) — PASS, 2320 с, 89/89 шагов,
  897 тестов, ключевые 17/17, revision7_checks PASS. Код/тесты/configs/corpus копии == рабочий каталог (cmp).
- `python3 scripts/make_status.py --verify tmp/clean_verify/runs/verify` → artifacts/checks/revision7_verify/
  (+ clean_verify.log), status.json: 29 implemented / 9 simplified, verify_ok=true.
- submission.json: verify_command прежний; run_command → scripts/rev7_live_legal_slice.py (60/150000/900),
  rev6 перенесён в history.revision_6. Без RESEARCH_LLM_* — LLMConfigError до отправки (проверено).
- report.md переписан под rev7 (rev6-отчёт — в report-history.md без изменений текста);
  improvement-results.md §«Полный verify»; case-update.md §7.4.
## Незавершённое (переходит критику / следующему проходу)
- журналы критика rev5/rev6 для переразметки отсутствуют; живой срез H1 high_value не измерен (лимит 5/эп.);
  тип (a) маршрутизацией не лечится. См. artifacts/report.md §6–7.

## Блок 4 (стадия report, 2026-11-08)
- Прочитаны brief.md, handoff.json (step=report, verification ok=true @2026-11-07T19:44:51Z, checks пуст),
  workflow-contract.json, case/docs/guardrails_requirements.md, break-even.
- Гигиена: симлинков 0, секретоподобных файлов нет; после проверки контроллера код/артефакты не менялись.
- Проверки: unittest-подмножество rev7 — 299 OK (artifacts/checks/revision7_report/unittest_rev7_subset.log);
  run_command без RESEARCH_LLM_* → LLMConfigError (run_command_noenv.log); status.json валиден по контракту.
- report.md: стадия report, §3 — итог handoff.verification и проверки стадии, §8 — гигиена.
- status.json: статусы не менялись (29 implemented / 9 simplified); к 13 ID добавлен лог стадии report.
## Дальше
- критика rev7 (см. report.md §7).
