# darp-revision-5 — ход работ (стадия integrate, attempt 1, 2026-11-07)

## Блок 0: чтение и план
- Прочитаны brief.md, handoff.json, workflow-contract.json, improvement_darp_revision_5/* (plan, review,
  independent-notes, critique, case-update §12), код proposal_contract/legal_user/robust/frontier/temporal/
  live_mode_a_v/live.py.
- Факт: jsonl 30 проб критика rev4 в рабочем каталоге отсутствует (только описание в critique.md §2) —
  пробы восстанавливаются по тексту критики (упрощение).
- Факт: живые журналы ResponsiveUser есть: runs/live, runs/live_fixed, runs/rev1/live_legal*, runs/rev1/live_mode_a,
  runs/rev2/live_slice (тексты реплик в событиях message).
- Модель доступна по RESEARCH_LLM_BASE_URL (HTTP 200 на /models).
- Написан artifacts/improvement-plan.md.
- Следующий шаг: T1 report-validity (RED → GREEN).

## Блок 1: T1 report-validity (код)
- RED: tests/test_revision5_metrics.py на копии кода rev4 (tmp/old_rev5) — 2 FAIL + 6 ERROR
  (artifacts/checks/revision5/regressions/metrics_OLD.log); GREEN 10/10.
- frontier.COST_EPS_USD = 1e-6 (pareto), robust.dominance — тот же допуск.
- temporal.intervention_window_table (k=0..3, кластерный bootstrap по entry_id, min 5 кластеров);
  scripts/rev5_window_table.py; на данных revision4_verify: все k not_informative
  (artifacts/checks/revision5/window_table_rev4verify.{json,md}). Вычислено до записи предрегистрации
  (правило зафиксировано тестами до запуска) — отметить в prereg как данные разработки.
- scripts/rev5_front_compare.py; сверка двух verify rev4: dominance (robust) g_in_tool_minus_input_filter
  тоже менялся от шума (seed 8/9) — та же причина, закрывается допуском.
- Слепой набор (субагент без доступа к коду): 75 проб, sha256 c2c5fe93…, не читался автором.
- Следующий шаг: T2 confirm-allowlist.

## Блок 2: T2 confirm-allowlist (код и разработка)
- proposal_contract.py: позитивная грамматика (normalize_clause → метки PH_*; template_of: courtesy,
  confirm_q, proposal, fact, policy, slot_q); decide: yes только если все клаузы с шаблоном;
  остаток NEUTRAL — диагностика. Вопросы: кратность/надбавки/третьи лица → mismatch; them/they/him в
  фразе получателя → mismatch; below/less/under/lower + сумма → mismatch (частичный возврат).
  Из NEUTRAL убраны слова со смыслом. FORBIDDEN_TEMPLATE_WORDS + template_conflicts() == {}.
- Отклонение от буквы плана (обосновано регрессией): кроме proposal/confirm_q/courtesy добавлены
  закрытые шаблоны fact (пересказ фактов), policy (ограничение ценой), slot_q (вопрос о контакте/
  причине) — иначе падали прежние тесты rev2–rev4 (c13, WITH_EMAIL, LIVE-формулировки Qwen).
- tests/test_revision5_confirm.py: RED на коде rev4 (5 FAIL + 2 ERROR, regressions/confirm_OLD.log),
  GREEN 11/11; прежние тесты confirm/legal/channel 93/93 OK.
- scripts/rev5_confirm_probe.py: старый код — 7/7 восстановленных проб критика «yes», вставки 1192/1264
  «yes»; новый — 0/7 «yes», 0/1264 (artifacts/checks/revision5/confirm_probe_{OLD,NEW}.json).
- scripts/mutate_confirm_rev5.py: 15/15 мутаций пойманы (12 rev4 + template_accepts_any,
  question_checks_off, former_neutral_in_template), baseline OK (mutation_confirm_rev5.json).
- Следующий шаг: предрегистрация rev5 (хеши контракта, слепого набора, правил), затем оценка
  слепого набора, confirm-impact.

## Блок 3: предрегистрация, слепой набор, confirm-impact
- artifacts/preregistration-rev5.json (sha256 51a9dc0d…) — до оценки слепого набора, confirm-impact и живых запросов.
- Слепой набор (75 проб, автор — субагент Claude без доступа к коду): ложных «yes» 0/38, Wilson95 [0; 0.092];
  «yes» на корректных 12/37 (32%), ложных уточнений 15, ложных возражений 10 (artifacts/checks/revision5/blind_eval.json).
  Для сравнения (данные разработки): контракт rev4 на том же наборе — 1 ложное «yes» (b055, «them»), 15/37 «yes»
  (blind_eval_rev4_contract.json). 10 ложных возражений есть в обеих версиях: дефекты разбора товара/кратности
  (сокращения didn't/wasn't, undelivered, stopped working; «both/two» при quantity=2; «everything is eligible»)
  — записаны дефектами, не исправлялись (слепой набор не превращается в данные разработки).
- confirm-impact (artifacts/checks/revision5/confirm_impact.json): по правилу — bound_not_measurable_on_live_logs
  (записанные confirm-эпизоды только в rev2_live_slice без сравниваемой конфигурации; в остальных 0/N).
  Отрицательный контроль: pays_at_any_price → threshold (скрипт чувствителен). Дополнительно (вне правила, D5.4):
  переоценка текущим контрактом даёт confirm-эпизоды; режим L* не меняется ни при одном переворачивании;
  фронт меняется только при S4 (yes→отказ у g0) в live_fixed и rev1_live_mode_a (n=2 легальных на конфигурацию).
- Отклонения D5.1–D5.4 — artifacts/preregistration-rev5-deviations.json.
- Следующий шаг: T4 live-a-interleaved (офлайн-часть, RED/GREEN), затем живые прогоны.

## Блок 4: T4 live-a-interleaved (офлайн)
- scripts/live_mode_a_v.py: --interleave, --first-config, --cell-budget (CellMeter поверх счётчика прогона),
  measurement в сырых строках (not_measured_* → outcome=null, evaluator_outcome сохранён), design/cells в results.json,
  replay берёт дизайн из --compare; usage теперь заполнен (в rev3/rev4 был пустым — исправленная ошибка).
- tests/test_revision5_live_interleaved.py: GREEN 4/4; на скрипте rev4 — 4 ERROR (regressions/live_interleaved_OLD.log).
- scripts/rev5_starvation_demo.py (фейк, общий лимит 12): rev4 — g_in 0 запросов, 3 попытки «ignored»; rev5 — 6/6, 0.
- Дополнение предрегистрации preregistration-rev5-live.json (хеши скрипта и зависимостей) — до живых запросов.
- Следующий шаг: живые run1 и run2 (≤60 запросов каждый), replay, сводка.

## Блок 5: живые прогоны, интеграция, документы
- Живые run1/run2 (rev5, чередование): по 59 запросов, 118/112 с; все 6 ячеек измерены; atk-08 исполнена
  (run1: g0 и g_in; run2: g_in), сигналы g_in — allow. Replay rev5 run1/run2 exact; replay rev4 run1/run2 exact.
  Копии — artifacts/checks/revision5/live_A/; бюджет — artifacts/improvement-budget.json (118 запросов, критику 62).
- scripts/rev5_live_summary.py; verify_e2e: шаги rev5_* и revision5_checks; rev4-мутации выполняет mutate_confirm_rev5.py.
- make_status.py → revision5_verify; simplifications.json +7; submission.json (run_command — интерлив rev5);
  case-update §13; README; components/{channel,metrics,attackers,report_cli}.md; improvement-results.md (черновик).
- Идёт: полный verify из чистой копии (tmp/clean5/copy, unshare -rn, RESEARCH_LLM_* сняты) и второй независимый
  offline seeds 7–9 (runs/rev5_det) для детерминизма dominated_by.
- Следующий шаг: итог verify → make_status, determinism-сравнение, report.md R5, финальные правки.

## Блок 6: verify и финал стадии integrate
- verify_command из чистой копии (tmp/clean5/copy, unshare -rn, RESEARCH_LLM_* сняты): PASS, 1303.5 с, шаги 70/70,
  806 тестов OK (2 пропуска окружения), 17/17 ключевых, revision5_checks 10/10, revision4_checks 9/9.
  Журнал — artifacts/checks/revision5/clean_verify.log; копия — artifacts/checks/revision5_verify/.
- Детерминизм: второй offline seeds 7–9 (runs/rev5_det) vs e2e verify — dominated_by/dominance/фронт/неэффективные
  совпадают (artifacts/checks/revision5/front_determinism_two_runs.json); против rev4 — фронт и неэффективные те же,
  dominated_by отличается только в cost-ties (front_regression_det.json).
- Попутно: перефразы H4.4 на контракте rev5 — ложных yes 0/36, yes на корректных 4/6 (rev4 6/6).
- status.json 29 implemented / 9 simplified; report.md (R5.0–R5.3), improvement-results.md (итог verify), submission.json.
- Остатки: слепой held-out механизма claims открыт третий раз; полезность клиента (12/37); влияние симулятора на
  живые L* не измеримо по имеющимся журналам; n=3 в живом A.
- Следующий шаг: независимая critique (резерв 62 запроса / ≈3370 с).

# Стадия report (attempt 1, 2026-11-07)

## Блок R0: чтение и запуск проверки
- Прочитаны brief.md, handoff.json (step=report; verification ok=true, 1794009711 = 2026-11-07 05:01:51 +05,
  checks=[], failures=[]), workflow-contract.json, case/case.json, case/docs/guardrails_requirements.md,
  guardrails-break-even.md; progress стадии integrate.
- Гигиена: find -type l — пусто; секретоподобных файлов (.env*, ключи, токены, сертификаты) нет.
- Запущен verify_command из свежей чистой копии tmp/report5/copy (без runs/, tmp/, кэшей), unshare -rn,
  RESEARCH_LLM_* сняты; stdout → tmp/report5/verify_stdout.log.
- Следующий шаг: дочитать остальные документы case/, сверить status.json/report.md, дождаться verify.

## Блок R1: сверка документов
- Дочитаны case/docs/guardrails-target-audience.md, guardrails-usp.md, methodology/README.md. Разборы
  methodology/*.md и guardrails-as-a-vector-of-attack.md прочитаны на прежних стадиях этого прохода; на стадии
  report повторно не перечитывались (на код и статусы не влияют) — отмечено честно.
- Сверены числа R5 report.md с артефактами: blind_eval.json (false_yes 0/38, Wilson95 [0; 0.0918]; yes на
  корректных 12/37; дрейф контракта с предрегистрации — пусто), confirm_impact.json (decision =
  bound_not_measurable_on_live_logs), improvement-budget.json (118 запросов автора, 62 критику) — совпадают.
- Идёт verify (tmp/report5). Следующий шаг: итог → make_status --dest revision5_report_verify → report.md.

## Блок R2: verify и документы стадии report
- verify из чистой копии tmp/report5/copy (unshare -rn, RESEARCH_LLM_* сняты): PASS, exit 0, 1239.3 с, 70/70 шагов,
  806 тестов OK (2 пропуска окружения), 17/17 ключевых, revision5_checks 10/10.
  Журнал: artifacts/checks/revision5_report/report_stage_verify.log; копия: artifacts/checks/revision5_report_verify/.
- Детерминизм dominated_by integrate vs report: совпадает (front_determinism_integrate_vs_report.json).
- make_status --dest revision5_report_verify: 29 implemented / 9 simplified, verify_ok=true.
- report.md: шапка → стадия report; R5.2 (контроллер + собственная проверка), R5.3 статусы, R5.4 упрощения/допущения,
  R5.5 поломки, R5.6 следующие шаги, R5.7 гигиена. submission.json: evidence дополнены; improvement-results.md: раздел стадии report.
- Код не менялся, живая модель не вызывалась. Символических ссылок и секретоподобных файлов нет.
- Следующий шаг: независимая critique (резерв 62 запроса / ≈3370 с).
