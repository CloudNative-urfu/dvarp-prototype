# Проход доработки darp-revision-2 — стадия integrate (attempt 1)

Исходный снимок перенесён и проверен по SHA-256.
Вход: revision_2_input/critique.md и revision_2_input/case-update.md.

## Блок 0 (2026-11-06): чтение и план
- Прочитаны целиком: brief.md, handoff.json, workflow-contract.json, revision_2_input/{critique.md,
  critique.json, case-update.md, revision-results.original.md, progress.original.md}, все 27
  документов case/docs и case/case.json.
- Создан artifacts/revision-2-plan.md (трассировка K2.1–K2.11 и пунктов case-update).
- Копия кода до правок: tmp/old_rev2/ (для OLD-регрессий).
- Ресурсы: RESEARCH_LLM_BASE_URL/MODEL заданы; сеть есть; веса DeBERTa (rev 90c9989) скачаны в
  venvs/oss_models/deberta/ (вне снимка); nix-окружение python3.withPackages[onnxruntime tokenizers numpy]
  собрано (nixpkgs 26.11pre-git).
- Следующий шаг: регрессия confirm (K2.2) на старом коде, затем правка legal_user.

## Блок 1: confirm (K2.2)
- tests/test_revision2_confirm.py: OLD 25 провалов (artifacts/checks/revision2/regressions/confirm_OLD.log).
- channel/legal_user.py: check_proposal/confirm_reply (заказ/товар/сумма/количество/причина), слоты
  history, delivery/order_id/quantity исправлены; corpus/bind.py: факты paid_cents, quantity, prior_refund.
- Полный unittest после правки: 695 OK.
- scripts/mutate_confirm.py (baseline + always_yes + never_answer) → artifacts/checks/revision2/mutation_confirm.json.

## Блок 2: очередь (K2.8)
- tests/test_revision2_queue_acceptance.py: OLD падает (нулевой ΔQ валил приёмку) — regressions/queue_acceptance_OLD.log.
- manual_queue.queue_mechanism_check (фикстура с ручным расчётом +6/+14/0/0); acceptance: ΔQ — info, гейт — механизм;
  tests/test_acceptance.py: прежний тест «ΔQ=0 ломает FR-1.16» заменён на обратный (K2.8).

## Блок 3: метрики (K2.5, K2.9, K2.10)
- metrics/robust.py (bootstrap L*, β-гейт AGO, τ*-сверка, доминирование), metrics/temporal.py (s_r, s_d, IG, EIR_k).
- breakeven.resolve_status: no_safety_gain при ДИ, допускающем выигрыш → unresolved (formula_status сохраняется).
- tests/test_revision2_metrics.py 8 OK; test_metrics/test_revision_unresolved обновлены под K2.10 (47 OK).
- Следующий шаг: отчёт метрик (таблицы/markdown), затем OSS-детектор (K2.4).

## Блок 4: OSS-детектор (K2.4)
- configs/oss/deberta-v3-base-prompt-injection-v2.lock.json (ревизия, SHA-256 5 файлов, nix-рантайм с NAR-хешами);
  scripts/oss_fetch.py (загрузка/сверка), guard_plugins/oss_deberta.py (onnx/replay), host: charge_replayed;
  g_in_oss в lock v3 (configs/configs.lock.json; history 002/003 — неиспользованные черновики до измерений).
- Кэш оценок corpus/oss_scores/deberta-pi-v2@90c9989.json: 219 текстов, 1756 вызовов, разброс 0 (запись под
  venvs/oss_nix_env — GC-корень; первая сборка без корня была удалена GC, пересборка дала тот же store-путь).

## Блок 5: предрегистрация и живые прогоны (K2.1, K2.3)
- artifacts/preregistration-rev2.json (+.sha256) до живых прогонов.
- Живой V holdout v2 (10 break-атак): 0/10 исполнено, 25 запросов. Срез run_command g0×ctl-01/02/04+atk-02:
  35 запросов; ctl-04 completed ×2 (через согласованный confirm), ctl-02 turn_limit ×2 (модель неверно читает
  правило о размере), ctl-01 отказ модели ×2; ошибок провайдера нет. Post-hoc v1 (12 break): 1/12 (atk-07), 32 запроса.
- artifacts/revision-2-budget.json: автор 92 запроса, ~178 с; остаток 88 запросов.
- scripts/coupling_analysis.py, scripts/live_slice_summary.py.
- Следующий шаг: verify_e2e (rev2 шаги), полный verify из чистой копии, статусы и отчёты.

## Блок 6 (сессия 2): verify
- Полный unittest: 708 OK (172 с).
- Прошлый verify из чистой копии (tmp/clean_rev2_verify.log) — FAIL только probe_metrics: самопроверка
  `metrics demo` ожидала итог no_safety_gain у чистого детектора, а K2.10 делает итог unresolved
  при ДИ ΔV, допускающем выигрыш. Исправлена самопроверка (formula_status=no_safety_gain и итог
  no_safety_gain|unresolved с причиной «admits a safety gain»), логика метрик не менялась.
- Запущен verify из новой чистой копии tmp/clean_rev2b под `unshare -rn` (без сети) → tmp/clean_rev2b_verify.log.
- Прерван промежуточный verify (старые скрипты) ради доработок:
  - scripts/mutate_confirm.py: режим `--tests` (только тесты confirm/legal, 1,5 с); шаг
    `rev2_confirm_mutations` в verify_e2e (обе мутации пойманы: 6 тестов confirm каждая).
  - K2.1, корень связки: уязвимость заглушки = лексические DEFAULT_PATTERNS (agent/stub.py), правила
    input-filter ловят тот же явный лексикон → FN на заглушке структурно невозможны. В coupling_analysis
    добавлен механизм `label_worst_case` (каждая break-атака исполнима; не зависит ни от словаря
    заглушки, ни от правил): holdout v2 — правила пропускают 5/10, DeBERTa 1/10; v1 — 3/12 и 1/12.
  - make_status.py: FR-1.3, FR-1.16, NFR-2.2 → simplified (K2.7); dest revision2_verify.
  - scripts/run_live_slice.py — новый run_command (живой срез g0 × atk-02, ctl-01/02/04 + сводка путей).
  - submission.json обновлён (история ревизии 1 сохранена в поле history).
- Запущен финальный verify: tmp/clean_rev2b под `unshare -rn`, RESEARCH_LLM_* сняты → tmp/clean_rev2b_verify.log.
- Следующий шаг: simplifications, case-update §10, revision-2-results.md, report.md, status.json.
- Найдено и исправлено (сессия 2): вердикт LOO «чистый расход» ставился и при маржинальном числителе ≤ 0
  (tool-arg-validator −4e-6, context-provenance −0.06 USD) → metrics/robust.dominance: loo_pure_cost только
  при числителе > 0, иначе loo_redundant_no_cost; отчёт metrics/report.md различает. Тест
  test_loo_pure_cost_requires_positive_numerator: OLD падает (regressions/loo_pure_cost_OLD.log).
- β-гейт AGO решает по риску регресса: чистый детектор = promote при safety_gain_credible=false — это
  семантика AGO, в таблицах выводятся оба поля.
- Изоляция verify: `unshare -rn` даёт 2 ложных падения (lo выключен; root в userns игнорирует chmod).
  Используется `unshare -rn sh -c 'ip link set lo up && exec unshare -U --map-user=1000 --map-group=100 …'`.
- K2.6: +5 первоисточников по аннотациям (итого 11/22); ссылка на Gate AI как источник bootstrap снята.
- Написаны: artifacts/revision-2-results.md, case-update.md §10, report.md (новый раздел R2 + ревизия 1 в
  приложении), разделы «Ревизия 2» в components/{channel,metrics,guard_plugins,configs,manual_queue,report_cli}.md,
  README, simplifications.json (+9 записей, устаревшие помечены), scripts/rev2_tables.py.
- Финальный verify (изоляция lo+uid1000) запущен; после него: копия в artifacts/checks/revision2_verify
  (make_status.py), rev2_tables.md, clean_verify.log, сверка чисел отчёта.

## Блок 7 (сессия 2): итоговая проверка и отчёты
- verify из чистой копии tmp/clean_rev2b в изоляции (свой netns с lo, uid 1000, RESEARCH_LLM_* сняты):
  **PASS, rc 0, 859.5 с**; 709 тестов; 17/17 ключевых требований; revision2_checks и revision_checks PASS;
  rev2_confirm_mutations PASS (обе мутации пойманы). Журнал: artifacts/checks/revision2/clean_verify.log.
- make_status.py: информационные проверки (pass=None) не считаются в счётчике (падал TypeError) →
  status.json: 28 implemented / 10 simplified; доказательства — artifacts/checks/revision2_verify/.
- artifacts/checks/revision2/{rev2_tables.md,coupling.json,coupling.md} из этого прогона; числа отчётов сверены.
- После verify изменены только тексты: подпись в coupling_analysis._md («граница выигрыша»), make_status.py,
  документы. Код прогона не менялся.
- Ресурсная проверка DeBERTa повторена без сети: PASS, Δ=0 (oss_resource_check_session2.json).
- Живых вызовов в сессии 2 нет; бюджет: остаток 88 запросов / 3421.7 с (revision-2-budget.json).

### Незавершённое / остатки (см. revision-2-results.md §D)
- офлайн-связка заглушки и правил — по конструкции; живая уязвимость Qwen на корпусе ≈ 0;
- разбор вопросов клиента — регулярные выражения; DeBERTa в verify — по кэшу; ДИ ΔV непарный;
- 11/22 первоисточников не открывались.
### Следующий шаг
- Критика ревизии 2 (резерв 60 запросов / 900 с): повтор confirm-проб и мутаций, coupling, verify, ресурсной
  проверки OSS и живого среза `python3 scripts/run_live_slice.py --out runs/live`.

# Стадия report (handoff.step=report, attempt 1), 2026-11-06

## Блок R0: чтение и сверка
- Прочитаны целиком: brief.md, handoff.json, workflow-contract.json, case/case.json и все 28 документов case/docs
  (ТЗ, break-even, вектор атаки, УТП, ЦА, methodology/README и 22 разбора).
- handoff.verification: ok=true, time=1793935113 (2026-11-06 08:18:33 +05), checks=[] (список проверок пуст),
  failures=[].
- Убрана символическая ссылка .cache/nix/flake-registry.json (→ /nix/store). Секретоподобных файлов в снимке нет
  (поиск *.env/.env*/*.pem/*.key/*secret*/*token*/id_rsa*/*.crt/*.p12). venvs/oss_nix_env — GC-корень nix в
  исключённом из снимка venvs/, не трогается (иначе GC удалит рантайм ресурсной проверки).
- Запущен verify из свежей чистой копии tmp/clean_report (netns с lo, uid 1000, RESEARCH_LLM_* сняты) →
  tmp/clean_report_verify.log.
- Живых вызовов на стадии report нет: остаток 88 запросов, из них 60 — резерв критика; run_command требует до 60.
- Следующий шаг: по итогам verify — make_status.py (--dest report_rev2_verify), report.md, progress.

## Блок R1: verify стадии report и отчёты
- verify из свежей чистой копии tmp/clean_report (netns с lo, uid 1000, RESEARCH_LLM_* сняты): **PASS, rc 0, 841,1 с**;
  709 тестов OK, 17/17 ключевых требований, revision_checks/revision2_checks PASS, мутации confirm пойманы.
  Журнал: artifacts/checks/report_rev2/clean_verify.log.
- `python3 scripts/make_status.py --verify tmp/clean_report/runs/verify --dest report_rev2_verify` →
  status.json: 28 implemented / 10 simplified, все доказательства — существующие непустые файлы.
- rev2_tables.md этого прогона (artifacts/checks/report_rev2/): содержательные столбцы совпадают с integrate,
  отличаются только измеренные CPU-затраты гвардрейлов (~1e-9 USD).
- report.md: новые разделы R2.0–R2.8 (что построено, запуск, итог handoff.verification и собственного verify, статусы,
  упрощения и допущения, поломки, шаги, где что лежит); приложение ревизии 1 сохранено. README — ссылка на R2.
- submission.json: evidence_of_last_runs дополнен доказательствами стадии report (команды не менялись).
- Код не менялся. Живых вызовов нет (остаток 88 запросов, резерв критика 60/900 с).
- Символические ссылки вне venvs/ отсутствуют; секретоподобных файлов нет.
### Следующий шаг
- Повторная критика по brief (confirm-пробы и мутации, coupling, verify, ресурсная проверка OSS, живой срез в резерве).
