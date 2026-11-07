# darp-revision-6 — прогресс (стадия integrate)

## Сделано
- Прочитаны brief, handoff, контракт, improvement_darp_revision_6/*, case/docs (основные + README методологий).
- Архив rev5: artifacts/revision-5-{plan,results}.md, revision-5-budget.json, artifacts/history_rev5/.
- План: artifacts/improvement-plan.md.
- T1 server-invariants-v2: boundary.py (boundary-2 spec/fingerprint, карта отличий), executor.py
  (параметр boundary, R1/R2/R5 в BEGIN IMMEDIATE, авторизатор доверенных таблиц), schema.py (DDL_V2),
  state_store/trusted.py, fixtures (ключ trusted), EpisodeStateManager(schema_version=2).
  tests/test_server_invariants_v2.py — 23 теста OK (5 повторов).
- lock v4 (configs/configs.lock.json): конфигурации и set_fingerprint те же, изменился только
  defense_code.executor; lock v3 → configs/history/configs.lock.004.*.json.
- T2: evaluator-2 (state.py/evaluator.py), agent_demo/legal_suite.py, tests/test_foundation_v2.py OK.
- Runner: RunPlan.foundation (v1/v2), RunPlan.legal_confirm (facts по умолчанию), CLI --foundation,
  --legal-confirm. Регрессия seed 7 (g0,g_in,g_proc,g_simple): grammar_rev5 → fingerprint_flows ==
  rev5; facts → g_simple отличается текстом 5 легальных эпизодов, исходы те же.
- T4 (код): legal_user confirm_mode facts, docstring proposal_contract.

## Проверки
- python3 -m unittest tests.test_server_invariants_v2 tests.test_foundation_v2

## Следующий шаг
- foundation table script, мутации executor/evaluator v2, тесты T4, T5, RED на коде rev5
  (копия /home/geekiot/.local/share/research-campaigns/darp-revision-5/cycles/01/snapshot).

## Блок 2 (продолжение integrate, сессия 2)
- Проверено: test_server_invariants_v2 (23), test_foundation_v2 (11, +2 контроля oracle: evidence
  pending/rejected, одобрение < суммы), test_legal_facts_mode (8), test_revision6_live_truncation (3) — OK.
- rev6_relabel_live.py дозапущен: 5 источников, ignored_while_not_measured=0 (rev5_critic отсутствует).
- scripts/mutate_rev6.py (группы executor_v2/evaluator_v2/facts_mode): 23/23 неэквивалентных убиты,
  2 эквивалентных объявлены с причиной → artifacts/checks/revision6/mutations.json.
- Следующее: RED на коде rev5, rev6_confirm_mode_compare, rev6_boundary_diag + evaluation-results.md,
  preregistration-rev6, production-readiness.md, интеграция в verify_e2e, документы.
- preregistration-rev6.json (+.sha256) зафиксирована до прогонов T3/T4; отклонение D6.1 (классификация
  отказов по GT в rev6_boundary_diag.py после первого прогона, числа не меняются).
- T3 диагностика: runs/rev6_boundary_diag (12 прогонов), artifacts/evaluation-results.md,
  artifacts/checks/revision6/boundary_diag.json. Контроль V(g0) claims heldout_v3 v1 = 0.47/0.40/0.47 (совпал).
  v2: 0 нарушений; 15 серверных отказов на легальном ctl-06 = предотвращённая ошибка заглушки (GT refusal),
  итог lost; ложных блокировок 0.
- production-readiness.md написан.
- В работе: rev6_confirm_mode_compare (grammar_rev5 готов, facts идёт).
- T4 сравнение: runs/rev6_confirm_compare, artifacts/checks/revision6/confirm_mode_compare.json: 57 ячеек,
  task_status одинаков 57/57, lost 0/0, пересказов facts 30 (только g_simple просит подтверждения), токенов
  одобрения в них 0; регрессия fingerprint_flows rev5 seed 7 (grammar_rev5) — совпала. Отклонение D6.2.
- verify_e2e.py: блок revision6 (шаги rev6_* и revision6_checks). make_status.py: REV6 дополнения, dest revision6_verify.
- simplifications.json +8 записей rev6; improvement-budget.json новый (0 запросов).
- Далее: полный verify из чистой копии (tmp/clean_verify, isolated_verify.sh), затем status/report/results.
- Полный verify №1 (tmp/clean_verify): unit_tests FAIL 3 — test_executor (сигнатура Executor без boundary;
  AGENT_MESSAGES = DENIAL_CODES v1) и test_revision2_confirm (фейк ждёт «yes», а план по умолчанию facts).
  Исправлено: тесты уточнены (boundary — только усиление, коды v2 ⊇ v1; тест rev2 закреплён на grammar_rev5
  + новый test_facts_mode_never_approves). Локально 852 теста OK. Нужен повторный verify из чистой копии.
- verify №1 итог: FAIL (1959 с) — unit_tests, probe_rev2_confirm, rev5_confirm_mutations/revision4/5_checks
  (базовая копия падала на том же тесте rev2), revision3_checks (configs.lock.json = lock v4 не объявлен).
  Все шаги rev6 PASS, 17/17 ключевых PASS. Исправлено: тесты; D6.4 + file_hash_updates в
  preregistration-rev6-deviations.json, verify_e2e читает их для проверки rev3 (исторический файл rev3 не тронут).
- verify №2 запущен: tmp/clean_verify, лог tmp/clean_verify2.log.
- verify №2: unit_tests PASS (852), revision3 PASS; revision5_checks FAIL — слепой набор rev5 проверяет хеши
  proposal_contract.py/legal_user.py (изменены одобренной T4). Ответы grammar_rev5 на слепом наборе = rev5
  построчно (75/75). D6.5 + file_hash_updates; verify принимает дрейф только объявленный и при равенстве строк.
  verify №2 остановлен (не мог дать PASS); запущен verify №3 (tmp/clean_verify, лог tmp/clean_verify3.log).

## Итог стадии integrate (сессия 2)
- verify №3 из чистой копии под isolated_verify.sh: PASS, 1933 с, 78/78 шагов, 852 теста (0 пропусков),
  17/17 ключевых, revision6_checks 9/9, revision5 10/10, revision4 9/9, revision3 13/13; фронт {g_simple} 7/8/9.
  Логи: artifacts/checks/revision6/verify_logs/; копия: artifacts/checks/revision6_verify/.
- status.json (make_status.py --dest revision6_verify): 29 implemented / 9 simplified, verify_ok=true.
- Документы: report.md (R6.0–R6.7), improvement-results.md, production-readiness.md, evaluation-results.md,
  case-update.md (§6), README.md, components/*.md, submission.json (run_command: live A, бюджет ячейки 13,
  atk-06/atk-08, D6.3), simplifications.json (+8), improvement-budget.json (автор 0 запросов).
- Не сделано: опциональный живой легальный срез (причина — в budget/results); RED на копии кода rev5
  заменён мутантами-возвратами (simplifications).
- Проверка: python3 scripts/verify_e2e.py --out runs/verify (или sh scripts/isolated_verify.sh).
- Следующий шаг: критика ревизии 6 (весь живой бюджет 180/3600 у критика).

## Ремонт integrate (попытка 2)
- Причина сбоя попытки 1: «Snapshot size limit exceeded» — runs/ занимал 2.0 ГБ (снимок rev5 при 871 МБ проходил).
- Корень: сырые прогоны rev6_confirm_compare (845 МБ), rev6_boundary_diag (369 МБ) и побайтные копии
  прогонов rev5 (rev5_det, report_cli_full, rev1, rev3_mechanism, verify_unshare; ~660 МБ).
- Исправление: rev6-прогоны → runs/archive/*.tar.xz (74+34 МБ, SHA256SUMS); копии rev5 (diff -rq с
  darp-revision-5/cycles/01/snapshot/runs — идентичны) → манифесты runs/archive/<имя>.sha256; README в
  runs/archive. Снимок без tmp/venv: 335 МБ (после verify ≈ +80 МБ). verify из чистой копии эти каталоги
  не читает (tmp/clean_verify №3 содержал только runs/verify).
- simplifications.json/status.json: +1 запись (runner, архивирование прогонов). evaluation-results.md: ссылка на архив.
- Проверка: полный verify из чистой копии tmp/clean_verify4 (лог tmp/clean_verify4.log).
- Итог ремонта: verify из чистой копии tmp/clean_verify4 — PASS, 1936.6 с, все шаги PASS, 17/17 ключевых
  требований PASS; runs/verify в копии 203 МБ → ожидаемый снимок ≈ 415 МБ (< 871 МБ прошедшего снимка rev5).
  Лог: artifacts/checks/revision6/verify_logs/integrate_repair2_clean_verify.log.
- Следующий шаг: критика ревизии 6.

## Стадия report (ревизия 6, attempt 1)
- Прочитаны brief/handoff/контракт; case: requirements, break-even, vector-of-attack, usp, target-audience,
  methodology/README (разборы отдельных работ читались на прежних стадиях; на статусы не влияют).
- handoff.verification: ok=true, 2026-11-07 17:08:07 (+05), checks=[] (контроллер деталей не дал).
- Гигиена: симлинков 0; секретоподобных файлов (.env*, secrets/, ключи, сертификаты, *token*) 0.
- status.json проверен по контракту: 38/38 ID, evidence существуют и непусты, simplifications == simplifications.json (216).
- Запущен verify №5 из чистой копии под isolated_verify.sh: tmp/clean_verify5, лог tmp/clean_verify5.log.
- artifacts/report-history.md — полный прежний report.md (R6…R1) без изменений; report.md переписывается кратко.
- Следующий шаг: дождаться verify №5 → make_status.py --dest revision6_report_verify → report.md.
- report.md переписан (8 разделов: построено, запуск, проверка, статусы, упрощения/допущения, поломки,
  шаги, гигиена); плейсхолдеры VERIFY5/STATUS заполняются после verify №5.
- Проверено: run_command без RESEARCH_LLM_* → код 2 (LLMConfigError), сеть не трогает, но создаёт пустой --out (записано в поломки).
- verify №5 (tmp/clean_verify5, isolated netns+lo+uid): PASS, 1902.4 с, 78/78 шагов, 852 теста, 17/17 ключевых;
  результаты = verify №4 (boundary_diag results равны; L* отличается только CPU-членом ~1e-9).
  Лог: artifacts/checks/revision6/verify_logs/report_stage_verify5_PASS.log.
- status.json пересобран (make_status.py --dest revision6_report_verify): 29 implemented / 9 simplified, проверен по контракту.
- report.md заполнен итогами. Стадия report завершена; живая модель: 0 запросов.
- Следующий шаг: критика ревизии 6.
