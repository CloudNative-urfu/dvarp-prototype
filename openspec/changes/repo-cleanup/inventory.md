# Инвентарь: состав компонентов прототипа (этап 1, issue #2)

База — рабочее дерево после таски 1 (снимок ревизии 11 в корне). Скан зависимостей
выполнен по импортам и вызовам `scripts/*.py` из `darp_range/`, `tests/` и самого
`scripts/`; учтено, что каталоги `artifacts/checks/*`, `artifacts/rev10/*`,
`artifacts/history_rev10/*` в Git отсутствуют (не экспортировались кампанией).

Критерий решения: файл нужен, если на него ссылается живая цепочка — слим-верификация,
юнит-тесты стоящих контрактов, CLI (`report_cli offline/live`) или мутационный набор.
Всё, что проверяет только закрытые ревизии 1–9 или кампанную обвязку, удаляется;
оригиналы остаются в `revisions/darp-revision-N/` и под тегом `archive/rev11`.

## 1. Верификация

| Позиция | Решение | Обоснование |
| --- | --- | --- |
| `scripts/verify_e2e.py` (монолит, 1362 строки) | удалить | 18 зависимостей от артефактов вне Git; из чистой копии уже не проходит. Шаги ревизий 1–9 проверяют закрытые ревизии |
| `scripts/rev10_verify.py` | удалить | те же зависимости (`historical.py`, `artifacts/checks/revision7\|8`, prereg-rev8, бюджеты); стоящие контракты v2r/брокера/изоляции покрыты тестами `test_rev10_*`, `test_rev11_*` |
| `scripts/verify.py` (новый, ~200 строк) | добавить | слим-цепочка: env → unittest → configs frozen → corpus → пробы компонентов → мутации → e2e offline v2r 7,8,9 + verify → stress seed 7 → приёмка ключевых требований → `rev11_verify --skip-rev10` → compaction |
| `scripts/rev11_verify.py` | оставить | проверка текущего контура rev11 (shop-slice, брокер fail-closed); вызывается из слим-verify с `--skip-rev10` |
| `scripts/rev10_legacy_compare.py` | удалить | сравнение legacy v1 с замороженным референсом `artifacts/rev10/...` (вне Git) |

## 2. Скрипты `scripts/` (95 файлов)

### Удалить (47)

| Скрипт | Причина |
| --- | --- |
| `historical.py` | заморозка кода закрытых ревизий 7–8 (артефакты вне Git); нужен только монолиту и rev10_verify |
| `live_attack_probe.py` | одноразовая проба rev2, результат сохранён; сопоставление — через удаляемый `coupling_analysis` |
| `make_prereg_rev4/6/7/8.py` (4) | генераторы пререгистраций закрытых ревизий; сами JSON сохраняются |
| `make_status.py`, `report_stage_status.py` | кампанные сборщики статусов |
| `rev2_tables.py` | таблицы ревизии 2 |
| `rev3_mechanism_eval.py`, `rev3_metrics_behaviour.py` | аналитики ревизии 3 (результаты в отчёте) |
| `rev4_confirm_compare.py`, `rev4_live_summary.py`, `rev4_paraphrase_heldout.py`, `rev4_repeat_compare.py`, `rev4_stepshield_diag.py` | аналитики ревизии 4 |
| `rev5_blind_eval.py`, `rev5_confirm_impact.py`, `rev5_confirm_probe.py`, `rev5_front_compare.py`, `rev5_live_summary.py`, `rev5_starvation_demo.py`, `rev5_window_table.py` | аналитики ревизии 5 |
| `rev6_boundary_diag.py`, `rev6_cell_budget.py`, `rev6_confirm_mode_compare.py`, `rev6_foundation_table.py`, `rev6_relabel_live.py` | аналитики ревизии 6 |
| `rev7_boundary_diag.py`, `rev7_foundation_table.py`, `rev7_live_legal_slice.py`, `rev7_live_slice_summary.py`, `rev7_regression_rev6.py`, `rev7_relabel_live.py` | аналитики ревизии 7 |
| `rev8_live_legal_slice.py`, `rev8_live_slice_summary.py`, `rev8_order_scope_diag.py`, `rev8_patient_compare.py` | аналитики ревизии 8 |
| `rev10_status.py`, `rev11_status.py` | кампанные статусы по verify-выходам |
| `run_live_slice.py` | обёртка rev2 над `report_cli live` с фиксированными лимитами; живые режимы остаются через `live_mode_a_v.py` и `report_cli live` |
| `validate_decomposition.py` | кампанный контракт декомпозиции (решение владельца: не нужен) |
| `verify_e2e.py`, `rev10_verify.py`, `rev10_legacy_compare.py` | см. раздел 1 |

### Оставить (46 + новый verify.py)

| Группа | Скрипты | Почему |
| --- | --- | --- |
| Мутационный набор | `mutate_agent/attackers/channel/configs/confirm/confirm_rev4/confirm_rev5/corpus/evaluator/event_log/executor/guard_contract/guard_plugins/guard_policy/llm/manual_queue/metrics/report_cli/runner/simple_baseline/state_store(.sh)/tools/rev6/rev7/rev8/rev9/rev10` | стоящая RED/GREEN-защита текущего кода; `mutate_rev10` обрезается: убираются мутанты, патчащие `historical.py` |
| Текущий контур rev11 | `shop_slice.py`, `shop_slice_mutants.py`, `shop_slice_oracle.py` | жизненный цикл shop-svc/pay-emu/payout-worker, smoke-матрица, oracle, мутанты M1–M9 |
| Живые режимы | `live_mode_a_v.py`, `live_slice_summary.py` | драйвер живого прогона (режим A, replay, interleave), сводка завершений; тесты и мутанты ссылаются |
| Корпуса (provenance) | `make_holdout_v2.py`, `make_heldout_v3.py`, `make_trusted_v7.py`, `eval_holdout_v2.py`, `oss_record_scores.py`, `oss_fetch.py`, `oss_resource_check.py` | генераторы/оценка замороженных корпусов; `oss_fetch` упомянут из `darp_range/guard_plugins/oss_deberta.py` |
| Паспорта | `make_attack_cards.py`, `validate_attack_cards.py` | перегенерация/валидация `artifacts/attack-cards.json` |
| Сервисные | `compact_runs.py`, `isolated_unittest.sh`, `isolated_verify.sh`, `isolation_probes.py`, `isolation_verify.py` | компакция прогонов, изолированные прогоны, пробы изоляции (`darp_range/isolation/probe_run.py` ссылается) |

## 3. Тесты `tests/` (75 файлов)

Классификация 41 «ревизионного» теста: тесты стоящих контрактов текущих компонентов
(метрики, контракт подтверждения, легальный клиент, живые режимы, очередь, изоляция,
v2r-маршрутизация) сохраняются независимо от имени `revision*`/`rev*`; удаляются только
тесты, чей предмет — закрытая ревизия или удаляемый скрипт.

### Удалить (4)

| Тест | Причина |
| --- | --- |
| `test_rev10_historical.py` | проверяет `scripts/historical.py` |
| `test_rev10_legacy_compare.py` | импортирует `rev10_legacy_compare` |
| `test_revision4_live_summary.py` | импортирует `rev4_live_summary` |
| `test_revision8_live_slice.py` | импортирует `rev8_live_legal_slice`, `rev8_live_slice_summary` |

Примечание: `tests/rev11_broker_probe.py` остаётся — это текущая проба брокера
(fail-closed hang/flood), используется `test_rev11_broker_fail_closed.py` и
`scripts/shop_slice_mutants.py`.

### Адаптировать (3)

| Тест | Адаптация |
| --- | --- |
| `test_rev10_v2r.py` | `PrimaryFoundationTest` переписывается: проверка `PRIMARY_FOUNDATION == "v2r"` и шага `e2e_offline` на новом `scripts/verify.py` |
| `test_revision8_routing_neutrality.py` | хелперы `ScopeRecorder/routed_scope/summarize` инлайнятся из удаляемого `rev8_order_scope_diag` |
| `test_legal_facts_mode.py` | правок не требует: источники строк (confirm-тесты) остаются, ссылки на отсутствующие артефакты толерантны — фиксируется как контрольный кейс |

### Оставить (~70)

Все остальные, включая «ревизионные» по имени: `test_revision{2..5}_{confirm,metrics}`,
`test_revision2_queue_acceptance`, `test_revision3_mechanism`, `test_revision4_replay`,
`test_revision5_live_interleaved`, `test_revision6_live_truncation`,
`test_revision7_truncation_monotonic`, `test_revision8_append_only`,
`test_revision8_patient_customer`, `test_revision8_routing_neutrality` (после адаптации),
`test_revision_{acceptance,contour,legal_runner,legal_user,live_episode_scope,live_mode_a,unresolved}`,
`test_foundation_v2`, `test_server_invariants_v2`, `test_review_routing_v2r`,
`test_rev10_{broker_probes,broker_record,claim_matrix,contour,v2r}`, `test_rev11_*`.
Это контракты текущих компонентов: формулы L*/ΔQ, контракт подтверждения, граница v2r,
брокер fail-closed, append-only state store.

## 4. Артефакты `artifacts/` (1,9M)

### Оставить

| Файлы | Почему |
| --- | --- |
| `preregistration-*.json` (17) | протокол пререгистрации кампании — научно значимы (условие issue) |
| `attack-cards.json` | паспорта сценариев, используются отчётом и validate-скриптом |
| `report.md`, `report-history.md` | итоговый отчёт кампании и история |

### Удалить (~40)

| Файлы | Почему |
| --- | --- |
| `revision-{2..11}-{plan.md,budget.json,results.md}` (30) | рабочие копии; оригиналы в `revisions/darp-revision-N/` |
| `revision-plan.md`, `revision-results.md`, `revision-{9,10}-budget.json` | то же для ревизий 1 и 9–10 |
| `improvement-budget.json`, `improvement-plan.md`, `improvement-results.md` | кампанная программа улучшений |
| `decomposition.json` | контракт декомпозиции кампании (решение владельца) |
| `workflow-contract.json`-зависимые: `status.json`, `submission.json`, `simplifications.json`, `production-readiness.md`, `coevolution-progress.md`, `evaluation-results.md`, `openspec-results.json`, `case-update.md` | кампанная обвязка и одноразовые своды |
| `report-rev11-integrate.md` | перед удалением сверяется с `report-history.md`; если содержит уникальный итог — переносится в `docs/` |

## 5. Кампанная обвязка в корне

| Файл | Решение |
| --- | --- |
| `brief.md` | перенести в `docs/brief.md` (ТЗ прототипа) |
| `handoff.json` | удалить |
| `progress.md` | удалить |
| `workflow-contract.json` | удалить (вместе с `validate_decomposition.py` и `test_decomposition.py`) |

## 6. Компоненты `darp_range/` (стратегия A)

Все 28 модулей оставляются: у каждого есть живые импорты через тесты, CLI или живые
пути (`agent*`, `attackers`, `channel`, `configs`, `corpus`, `evaluator`, `event_log`,
`executor`, `guard_contract`, `guard_plugins`, `guard_policy`, `isolation`, `llm`,
`manual_queue`, `metrics`, `pay_emu`, `repro`, `report_cli`, `runner`, `shop_svc`,
`simple_baseline`, `state_store`, `svckit`, `tools`, …). В рабочем дереве правятся
только docstring-ссылки на удалённые скрипты (`metrics/report.py`,
`report_cli/acceptance.py`); замороженный defense-код не трогается — упоминания
`rev4_stepshield_diag` и пр. в его docstring остаются как исторические.

## 7. Итог по критериям issue

- Решение по каждой категории — этот документ.
- После очистки: `python3 scripts/verify.py` из чистой копии PASS (новая полная
  верификация), `python3 -m darp_range.report_cli offline` из чистой копии строит L*.
- Исходное состояние: тег `archive/rev11` на `develop` до влития очистки.
