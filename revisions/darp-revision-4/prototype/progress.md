# Проход доработки darp-revision-4

Исходный снимок перенесён и проверен по SHA-256.
Вход: revision_4_input/critique.md и revision_4_input/case-update.md.
Следующий шаг: план регрессий и исправления на integrate.

# Стадия integrate (attempt 1), ревизия 4, 2026-11-06

## Блок 0: чтение и план
- Прочитаны: brief.md, handoff.json, workflow-contract.json, revision_4_input/{critique.md, critique.json,
  case-update.md (= artifacts/case-update.md, diff пуст), progress.original.md, critic_scripts/*, critic_logs/*},
  case/docs/{guardrails_requirements.md, guardrails-break-even.md, methodology/stepshield.md, act-or-escalate.md}.
- Код до правок для RED: tmp/old_rev4/. Логи критика: artifacts/checks/revision4/critic_input/.
- План: artifacts/revision-4-plan.md.
- Следующий шаг: контракт подтверждения (P1), тесты RED/GREEN.

## Блок 1: контракт подтверждения (P1)
- darp_range/channel/proposal_contract.py: разбор всего сообщения на клаузы → роль → кадр → валидация
  полей (ok/mismatch/unknown) → решение; закрытый мир (остаток → уточнение); отрицатели не нейтральны
  (lexicon_conflicts пуст); отказ = глагол отказа или отрицатель + ≤3 слова + действие; получатель —
  фраза после to/into при переводе денег; безвредные отрицания: вежливость, «no extra», причина
  клиента, «cannot exceed», «(not $38)». legal_user.check_proposal делегирует контракту; мёртвый код
  rev3 удалён; ResponsiveUser: confirm по контракту (c13), отказ без вопроса — как раньше.
- tests/test_revision4_confirm.py (15 тестов: 32+12 проб критика, 5 перестановок × 12 противоречий/4 неизвестных
  × 4 вопроса, 18 отрицаний, 162 сочетания полей, позитивы, вопросы о почте, контракт).
  RED: artifacts/checks/revision4/regressions/confirm_OLD.log (13/15 FAIL), GREEN: confirm_NEW.log.
- Прежние тесты confirm/legal/channel: 82 OK. Скрипты критика заново: 0/32 FAIL, 12/12 → no
  (artifacts/checks/revision4/critic_scripts_rerun/).
- Мутации: scripts/mutate_confirm_rev4.py → 12/12 пойманы (mutation_confirm_rev4.json);
  scripts/mutate_confirm.py — якоря обновлены (4/4 пойманы).
- Сравнение до/после: scripts/rev4_confirm_compare.py → confirm_compare.json (old: 188 ложных yes, 285 ошибок — в attempt 1 ошибочно записано 178; new: 0 ошибок;
  это данные разработки).
- Следующий шаг: P2 метрики (τ* A4, bootstrap, StepShield). Позже: held-out перефразы чужой моделью (предрег.).

## Блок 2: метрики (P2)
- τ*: robust.escalation_ppv → new_violations, ppv_net; act_or_escalate → A4_no_new_violations, applicability
  (in_scope / out_of_scope:A… / not_applicable), L_tau_net, consistent_net. Вывод A4 в docstring.
  Контрпример критика: consistent=None (не дефект), out_of_scope:A4, L_tau_net = 6.857 = L* (consistent_net=True);
  дефект внутри области (C_manual расходится) по-прежнему даёт consistent=False.
- bootstrap: режим по знаку ΔV, затем числителя; уверенно только при доле ≥ 97.5% ВСЕХ репликатов;
  новые режимы more_violations / no_safety_gain; 2/2/96 → sign_unstable (не уверенно); границы 97.5% тестированы.
- StepShield: диагностика (rev3 verify): 495 строк, n − s_r = 0 во всех, s_d − s_r ∈ {0, none} — терминальное
  нарушение (edge case авторов). stepshield_summary: applicability (n − s_r > k), not_applicable → EIR=None,
  сырые числа в degenerate_raw, IG сохраняется с пометкой (≤ 0 по построению). Старые тесты rev2/rev3 обновлены.
- Окно вмешательства (адаптация): temporal.step_timelines / intervention_window(_summary) / k_from_queue;
  scripts/rev4_stepshield_diag.py. На rev3 verify (seeds 7–9, g_in_detect_only): IFR_3 0.59 vs rand 0.35;
  k_queue = 227 шагов (S=8 мин, Δt_step 2.12 с по живым журналам) ⇒ тревога без удержания неосуществима.
- report.py: колонки применимости StepShield и τ*. Тесты: tests/test_revision4_metrics.py (14 OK);
  RED metrics_OLD.log (11 из 14 FAIL/ERROR), GREEN metrics_NEW.log. metrics/report_cli/rev2/rev3 тесты OK.
- Скрипты критика заново: artifacts/checks/revision4/critic_scripts_rerun/{metrics_counterexamples,stepshield_hand}_critic.json.
- Следующий шаг: P3 — кассета записи/воспроизведения живых траекторий, предрегистрация rev4, живые прогоны.

# Стадия integrate (attempt 2, ремонт), ревизия 4, 2026-11-06
Причина повтора: attempt 1 оборвалась после блока 2 (P3 и интеграция не завершены; handoff.failures пуст).
Состояние на входе: 777 unittest OK (tmp/full_unittest_1.log), кассета/replay/скрипты P3 написаны, но
предрегистрация rev4 не создана, живых прогонов rev4 нет, verify/submission/status/report не обновлены.

## Блок 3: P3 — предрегистрация, перефразы, живые прогоны
- artifacts/preregistration-rev4.json (sha256 5b84de7d…) создана ДО живых запросов (scripts/make_prereg_rev4.py).
- Сухой прогон: live_mode_a_v.py --offline-stub (tmp/rev4_dry) — OK.
- H4.4 перефразы: 7 запросов, 24 с; оценка замороженного контракта: false yes 0/36 (Wilson95 [0, 0.096]),
  correct yes 6/6; negation 3 no + 3 clarify; double 5 no + 1 clarify (artifacts/checks/revision4/heldout_paraphrases,
  rev4_paraphrase_eval.json).
- Живой run1 (≤55 запросов) с кассетой: artifacts/checks/revision4/live_A/run1 (в процессе).
- Новое: scripts/rev4_repeat_compare.py (H4.2, офлайн по файлам), шаг rev4_repeat_compare в verify;
  make_status.py → revision4_verify + файлы rev4 + заметки NFR-2.3/FR-1.20/1.23/1.24.
- run1: 55 запросов, 119.6 с, лимит прогона исчерпан (5 отказов до отправки на прогон, 3 — на эпизод);
  run2: 55 запросов, 143.2 с. Итого автор: 117 запросов / 287 с (artifacts/revision-4-budget.json), критику — 63.
- ДЕФЕКТ найден и исправлен (D4.1): replay создавал бюджет по умолчанию (60) вместо бюджета прогона (55) →
  3 ложных расхождения на run1. Теперь бюджет берётся из --compare results.json (budget_source).
  RED/GREEN: artifacts/checks/revision4/regressions/replay_budget_{OLD,NEW}.log; тест
  test_replay_uses_recorded_budget. Отклонение: artifacts/preregistration-rev4-deviations.json.
  После ремонта: replay run1 и run2 — exact (55/55, 0 дефектов).
- ДЕФЕКТ учёта: попытки, у которых общий лимит прогона кончился до первой реплики атакующего, в rows значились
  ignored. Новый офлайн scripts/rev4_live_summary.py помечает их not_measured_run_budget (предрег. исключение);
  тест tests/test_revision4_live_summary.py. Итог: run1 g0 V=1/2 сцен. (atk-11 не измерен), g_in не измерен
  совсем; run2 g0 1/3, g_in 1/2 (atk-11 не измерен). atk-08 (multi_turn, instruction_split) исполнена под g0
  в обоих прогонах и под g_in в run2 — без тревоги правил. Порядок g0→g_in в общем бюджете ⇒ g_in голодает
  первым: сравнение g0/g_in не заявляется.
- Повтор (H4.2): 7 расхождений исходов, первое расхождение запросов — №5, 15 одинаковых запросов с разными
  ответами ⇒ дефекты NFR-2.3 записаны (rev4_repeat_compare.json).
- verify_e2e: шаги rev4_repeat_compare, rev4_live_summary, rev4_live_summary_test + проверки в revision4_checks.

## Блок 4: интеграция
- Полный verify из чистой копии: tmp/clean/copy → tmp/clean/verify_stdout.log (в процессе).
- Следующий шаг: submission.json, make_status → revision4_verify, report.md, case-update §12, revision-4-results.md.
- verify из чистой копии: PASS, exit 0, 1202.5 с, 57/57 шагов, 781 тест OK (0 skip), 17/17 ключевых,
  revision4_checks 9/9 (artifacts/checks/revision4/clean_verify.log, artifacts/checks/revision4_verify/).
- make_status.py --verify tmp/clean/copy/runs/verify --dest revision4_verify → status.json: 29 implemented, 9 simplified.
- Сверка первоисточников (arXiv HTML StepShield 2601.22136, Act or Escalate 2604.08588) —
  artifacts/checks/revision4/source_check.json: формулы совпадают.
- Обновлены: submission.json (run_command → prereg rev4; history.revision_3), report.md (R4 сверху, R3 приложением),
  case-update.md §12, revision-4-results.md, revision-4-budget.json, simplifications.json (+5), components/*.md.

## Состояние
Стадия integrate ревизии 4 завершена со стороны автора. Незавершённое (вне этой стадии): независимая critique
ревизии 4 (резерв 63 запроса / ≈3313 с); слепой held-out; чередующийся дизайн живого A.

# Стадия report (attempt 1), ревизия 4, 2026-11-06
Вход: handoff.step=report, handoff.verification = {ok: true, checks: [], time 1793989995 = 2026-11-06 23:33 +05},
failures пуст. Прочитаны целиком brief.md, handoff.json, workflow-contract.json, case/case.json и все 28
документов case/docs (5 основных + 23 methodology).

## Блок R0: сверка состояния
- status.json: 38 требований, 29 implemented / 9 simplified, все evidence-файлы существуют; simplifications 200 = 200.
- submission.json: verify_command = python3 scripts/verify_e2e.py --out runs/verify;
  run_command = python3 scripts/live_mode_a_v.py --prereg artifacts/preregistration-rev4.json --out runs/live_A.
- Симлинков и секретоподобных файлов в рабочем каталоге нет (find -type l; .env*/ключи/токены/сертификаты — пусто).
  .cache/ и .home/ — служебные журналы MCP-инструментов (не секреты; вне снимка по смыслу, не трогаются).
- Запущен свежий полный verify из чистой копии: tmp/report_clean/copy → tmp/report_clean/verify_stdout.log.
- Следующий шаг: дождаться verify, make_status → artifacts/checks/revision4_report_verify, обновить report.md R4.3.

## Блок R1: черновик отчёта
- Черновик новой верхней части report.md: tmp/report_r4_head.md (R4.0–R4.8: что построено, запуск, итог
  контроллера и проверки, статусы, упрощения/допущения, поломки, шаги, гигиена). Приложения R3/R2/R1 не меняются.
- Перепроверено: D3.2 в коде остаётся («Customer service: refund…» → []); venv-каталогов в work нет.
- revision-4-budget.json: добавлен report_stage (0 живых запросов).
- Следующий шаг: по завершении verify — make_status --dest revision4_report_verify, подставить итог в R4.3.

## Блок R2: проверка и итоговые документы
- verify из чистой копии (tmp/report_clean/copy, RESEARCH_LLM_* сняты): PASS, exit 0, 1197.6 с, 57/57 шагов,
  781 тест OK (0 skip), 17/17 ключевых, revision4_checks 9/9. Журнал: artifacts/checks/revision4/report_stage_verify.log.
- make_status.py --verify tmp/report_clean/copy/runs/verify --dest revision4_report_verify → status.json:
  29 implemented / 9 simplified, verify_ok=true; доказательства — artifacts/checks/revision4_report_verify/ (новый каталог,
  прежние revision4_verify и др. не тронуты).
- report.md: новая верхняя часть R4.0–R4.8 (итог контроллера: handoff.verification ok=true, 2026-11-06 23:33 +05,
  checks=[]); приложения R3/R2/R1 без изменений. Резервная копия прежней версии: tmp/report.before_report_stage.md.
- Валидация: статусы и evidence status.json по контракту (38/38, все файлы непусты), simplifications совпадают,
  все пути из R4 существуют; симлинков 0, секретоподобных файлов нет.

## Состояние
Стадия report ревизии 4 завершена. Незавершённое (вне стадии): независимая critique ревизии 4 (резерв живой
модели 63 запроса / ≈3313 с не тронут), слепой held-out, чередующийся дизайн живого A, исправление D3.2.
