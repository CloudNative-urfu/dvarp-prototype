# darp-revision-8 — progress

## 2026-11-08, integrate rev8

Выполнено:
- Прочитаны brief, handoff, контракт, improvement_darp_revision_8 (critique, review, independent-notes,
  plan.json, evaluation-protocol.json, brainstorm), код executor/legal_user/rev7 slice/verify.
- Артефакты rev7 сохранены: `artifacts/revision-7-{plan,results}.md`, `artifacts/revision-7-budget.json`.
- План: `artifacts/improvement-plan.md`.

Следующий шаг: T1 (RED-проба до правки, затем триггеры + PRAGMA/ATTACH в авторизаторе).

## T1 history-append-only — сделано (офлайн)
- RED до правки: `artifacts/checks/revision8/red_before/test_revision8_append_only.log` (REPLACE, PRAGMA, ATTACH проходили).
- executor.py: `_install_append_only_triggers` (recursive_triggers=ON + TEMP-триггеры) до set_authorizer; авторизатор: ATTACH/DETACH, PRAGMA вне allow-list → DENY.
- tests/test_revision8_append_only.py — OK (seeds 7/8/9, 2/2r, v1 прежний). Тесты executor/routing/invariants — OK (117).
- Lock конфигураций v6 (`configs/configs.lock.json`, v5 → configs/history/configs.lock.006…): только defense_code.executor.

## T2 routing-neutrality-order-scope — код и тесты
- tests/test_revision8_routing_neutrality.py OK; scripts/rev8_order_scope_diag.py (проверен на seed 7, 2 корпуса).
Следующий шаг: T3 (legal_user facts_patient), затем mutate_rev8.py.

## T3 patient-legal-customer — код, тесты (сессия 2)
- legal_user.py facts_patient / agent_stalled, agent_demo/patient.py; tests.test_revision8_patient_customer OK.
- rev7_boundary_diag.py: --legal-confirm принимает facts_patient (единственная правка; объявить в deviations rev8).
- scripts/rev8_patient_compare.py (facts rev7 vs facts_patient, дельта, N2 refusal). Прогон диагностики facts_patient
  запущен: runs/rev8/boundary_diag_patient → artifacts/checks/revision8/boundary_diag_patient.json.

## T4 live-legal-slice-v2 — харнесс (офлайн)
- scripts/rev8_live_legal_slice.py, scripts/rev8_live_slice_summary.py, scripts/make_prereg_rev8.py.
- Найдена и исправлена ошибка: фабрика модели на эпизод → одинаковый ключ m0/<case> у пары, второй эпизод
  обрезался лимитом L. Теперь одна фабрика на запуск; мутант в mutate_rev8 (live_labels) убит.
- tests/test_revision8_live_slice.py OK (13): допуск 2L, лимит на эпизод, ticket_id пары, pair_check, replay
  кассеты фейкового транспорта побайтно, бюджет прохода, отрицательный контроль b_args на событиях rev7.
- scripts/mutate_rev8.py: 22/22 killed (лог пробы tmp/rev8/mut_try.json).
- artifacts/preregistration-rev8.json сейчас — ЧЕРНОВИК (копия tmp/rev8/prereg_draft.json); финальный
  генерируется после офлайн-гейтов и до первого запроса.
Следующий шаг: дождаться boundary_diag_patient → compare; order_scope полный; deviations rev8; verify-шаги; prereg; живой срез.

## Офлайн-гейты rev8 (сессия 2)
- patient_compare: 0 изменений task_status (2712 эп. на основу), N2 refusal 0/180; механизм сработал в 276 эп.
  (agent_stalled, nudges=2) — artifacts/checks/revision8/patient_compare.{json,md}.
- order_scope (заглушка 2r): вне заказа 0/26 атак, 0/108 легальных — artifacts/checks/revision8/order_scope.{json,md}.
- preregistration-rev8-deviations.json (D8.1–D8.6); verify: проверки prereg rev3/rev5/rev7 принимают объявления rev8,
  бюджет rev7 читается из revision-7-budget.json; добавлены шаги rev8_* и revision8_checks.
- production-readiness.md обновлён (rev7-версия в artifacts/history_rev7/).
- improvement-budget.json — учёт rev8 (пока 0 запросов).
- Запущен полный verify до живого среза: runs/verify_prelive_rev8 (лог runs/rev8/verify_prelive.log).
  Ожидаемо не пройдут только live-зависимые проверки rev8 (нет живых прогонов/summary).

## Pre-live verify и живой срез (сессия 2)
- verify до среза (runs/verify_prelive_rev8, 2940 с): все шаги PASS, кроме live-зависимых rev8_live_slice_summary и
  revision8_checks (не было живых прогонов и .sha256 предрегистрации) — artifacts/checks/revision8/verify_prelive/.
- preregistration-rev8.json sha256 e74d0c8d… записана до первого запроса.
- Живые запуски: run1 (40 запр.), run2 (34), run3 (38) = 112/120, 259 767 токенов, 215 с; 9/9 пар допущены.
  Копии: artifacts/checks/revision8/live_legal/{run1,run2,run3}, SHA256SUMS, summary.{json,md}.
- После run1: ошибка pair_check (случайные id tool_call провайдера) → D8.7 (+тест, +мутант); сводка пересчитывает
  пары по requests.jsonl (D8.8); пары с равными запросами и разными исходами → дефект (D8.9).
  artifacts/preregistration-rev8-post-deviations.json.
- Итог: H1 observed (high_value: v2 3/3 lost b_rule, 2r 3/3 completed; recent_qty2 2/3 и 2/3). Дефекты пар 4/9
  (недетерминизм модели). Replay кассет run1–3 точный.
Следующий шаг: документы (report, case-update, evaluation-results, improvement-results T4), status/submission,
изолированный verify из чистой копии.

## Документы и финальный verify (сессия 2)
- Обновлены: improvement-results.md (T1–T4), evaluation-results.md (R8.1–R8.3), case-update.md (§8.x), report.md
  (§0, §2, §6, §7; rev7-версии в artifacts/history_rev7/), submission.json (run_command → rev8 slice; rev7 в history),
  improvement-budget.json (112 запросов, 3 запуска), run_command без env → LLMConfigError
  (artifacts/checks/revision8/run_command_noenv.log).
- Запущен изолированный verify из чистой копии: tmp/clean_verify_rev8 (лог runs/rev8/clean_verify.log).
Следующий шаг: по завершении — копия доказательств (make_status.py --verify tmp/clean_verify_rev8/runs/verify),
status.json, report §3.
- Чистый verify №1 (tmp/clean_verify_rev8): PASS 2944 с. Найдено: проверка order scope в revision8_checks была
  пустой (ключа totals нет → None == None). Исправлено (сравнение table и out_of_order_by_entry, непустая таблица);
  пересчёт на выходе №1 — PASS по существу. Доказательства №1: artifacts/checks/revision8/clean_verify_run1/.
- Запущен чистый verify №2 с исправленной проверкой: tmp/clean_verify_rev8b, лог runs/rev8/clean_verify2.log.
- Чистый verify №2 (tmp/clean_verify_rev8b): PASS 2963 с, 101/101 шагов, 935 тестов, 17/17 ключевых.
  Доказательства: artifacts/checks/revision8_verify/. status.json пересобран (29 implemented, 9 simplified).
  report §3/§4, submission (evidence) обновлены.

## Итог стадии integrate rev8 — завершено
Все 4 одобренные задачи реализованы и проверены; живой срез 112/120 запросов; H1 observed (facts_patient).
Незавершённое (для следующего прохода): тип (a) на 2r; правило привязки к заказу — владельцу; n=3 на ячейку.

## Стадия report (2026-11-08, attempt 1)
- Прочитаны brief, handoff (step=report, verification ok=true, checks=[] в 08:51:08 +05), контракт, case/ (требования,
  break-even, README методологий; разборы методологий прочитаны на прежних стадиях, см. case-update.md).
- Гигиена: `find -type l` — 0; секретоподобных имён нет.
- status.json: 38/38 требований, evidence существуют (0 пропусков), simplifications.json == status.simplifications (237).
- Запущен свежий изолированный verify из чистой копии: tmp/clean_verify_report (лог tmp/rev8/clean_verify_report.log).
Следующий шаг: по завершении — копия доказательств в artifacts/checks/report_verify/, правка report.md (§0 стадия,
§3 handoff.verification и новый verify, §5 число упрощений 237).
- Verify стадии report: PASS 2973.5 с, 101/101 шагов, 935 тестов, 17/17 ключевых; копия
  artifacts/checks/report_verify/. report.md: заголовок (стадия report), §3 (handoff.verification + новый verify),
  §5 (237 упрощений). status.json без изменений (статусы и evidence сверены: 29 implemented, 9 simplified).
## Стадия report — завершено.
