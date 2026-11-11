# darp-revision-11 — progress (исполнитель, change returns-operator-payout-slice)

## Исправление D11-S1 — 2026-11-11

- Завершён минимальный фикс `worker.send`: исчерпание бюджета POST сохраняет GET-сверку.
- Новый `tests/test_rev11_payout_recovery.py`: RED 4 FAIL → GREEN 4 PASS, включая
  реальный SIGKILL последней принятой попытки, рестарт, pending → succeeded и одну выплату.
- Точечная регрессия без namespace: 9 PASS; evidence/команды — `artifacts/openai-fix/README.md`.
- Актуальные report/results дополнены; прежние полные verify относятся к коду до фикса.
- Логический блок готов координатору для независимой оценки, VM-проверки и коммита.
  Git не запускался; закрытие всей критики не заявляется.

## Сделано
- Прочитаны brief, handoff, contract, openspec change (proposal/design/specs/tasks), review rev11, код executor/
  state_store/tools/legal_suite/sandbox/runner/report_cli.
- Снимок артефактов rev10: artifacts/history_rev10/, revision-10-{plan,results}.md.
- artifacts/improvement-plan.md (rev11), artifacts/revision-11-budget.json (= improvement-budget.json), 0 живых запросов.

## Следующий шаг
- RED-тесты, затем darp_range/pay_emu, darp_range/shop_svc, worker, scripts/shop_slice*.py, broker-fail-closed.

## Блок 2 (код сервисов)
- darp_range/svckit (HTTP 127.0.0.1, ready.json, клиент, JSONL), darp_range/pay_emu (книга+процесс),
  darp_range/shop_svc (migrate v3, auth, service, server, worker, runtime, harness, CLI).
- Проверка вручную: reset/up/down; high_value → escalated → approve → payout succeeded → customer refunded;
  recent → agent create_refund → payout (триггер outbox) → refunded.
- Дальше: scripts/shop_slice_oracle.py, scripts/shop_slice.py (smoke-матрица), мутанты, тесты, broker-fail-closed.

## Блок 3 (срез: oracle, smoke, мутанты)
- scripts/shop_slice_oracle.py (I1–I8, mode=ro, без импорта darp_range), scripts/shop_slice.py (reset/up/down/
  status/smoke; 14 сценариев + reset_deterministic + busy_port_up + oracle_negative_control),
  scripts/shop_slice_mutants.py (M1–M7 убиты целевыми проверками, контроль без подмен PASS, ~19 с).
- Исправлены дефекты собственных проверок: порядок снимка хеша (operator_api_negatives), фоновый settle и
  сохранённый ответ 409 в книге (payment_emulator_contract), относительный --out в мутантах.
- Дальше: broker-fail-closed (sandbox recv timeout, stderr cap, Runner preflight, run_live factory), unit-тесты.

## Стадия integrate (handoff.step=integrate, attempt 1) — 2026-11-10
- Обнаружено: broker-fail-closed уже реализован (sandbox recv select+тайм-аут, Runner preflight), тесты rev11 зелёные,
  кроме LoopbackUnavailable: в netns без lo bind проходит, pay-emu висел. Ремонт: svckit.ServiceServer делает
  самоподключение после bind → LoopbackUnavailable (код 3). tests.test_rev11_shop_slice: 13/13 OK.
- smoke: PASS 17/17 (wall 15.8 с, CPU 4.6 с) → artifacts/rev11/smoke; mutants M1–M9: 9/9 killed, контроль OK (70 с).
- Дальше: scripts/rev11_verify.py + шаг в verify_e2e, rev10_verify, полный verify из чистой копии, артефакты.
- scripts/rev11_verify.py (5 задач × unit/smoke/мутанты/бюджет времени): PASS 97 с (artifacts/rev11/fast).
- rev10_verify: проверка «improvement-budget.json = копия rev9» устарела (rev11 по брифу ведёт общий файл) →
  сверяется архив history_rev10/improvement-budget.json. rev10_verify PASS 120 с (artifacts/rev11/rev10_regression).
- verify_e2e: добавлен шаг rev11_verify (--skip-rev10). Дальше: полный verify из чистой копии, артефакты.
- Документы case/ (5 основных + 23 методологических разбора) перечитаны целиком.
- Артефакты rev11: simplifications.json (+11 записей rev11), components/shop_contour.md (+разделы rev11 в state_store,
  executor, evaluator, attackers, runner, report_cli), coevolution-progress.md, openspec-results.json, tasks.md [x] (validate
  --strict OK → artifacts/rev11/logs/openspec-validate.log), improvement-results.md, case-update.md (§11),
  production-readiness.md (§00), evaluation-results.md (R11), lifecycle.log, scripts/rev11_status.py.
- Полный verify из чистой копии запущен в .runtime/clean_rev11 (unit_tests PASS 252 с, идут дальнейшие шаги).
- Дальше: дождаться verify → rev11_status.py → report.md §0 rev11, submission.json, budget; финальная проверка.
- Полный verify из чистой копии: PASS 3326.7 с, 109/109 шагов, 1024 теста OK, ключевые 17/17
  (artifacts/checks/revision11_verify/, clean_verify.log). rev11_status.py → status.json: 29 implemented, 9 simplified.
- submission.json: verify_command прежний (включает rev11_verify), change_check_command → rev11_verify, slice_commands.
- report.md (раздел rev11), бюджет (0 запросов). Проверка контракта артефактов OK; case/ без изменений.
- Стадия integrate завершена. Остатки — в report.md «Остатки» и coevolution-progress.md.

## Стадия report (handoff.step=report, attempt 1/3) — 2026-11-10
- Прочитаны brief.md, handoff.json, workflow-contract.json, все документы case/ (5 основных + README + 23 разбора) целиком.
- handoff.verification: ok=true, checks=[] (контроллер, 2026-11-10 14:27:32 +05), failures=[].
- Код после полного verify integrate не менялся (новее verify.json только отчёты, handoff, scripts/rev11_status.py).
- Удалены синтетические auth-файлы контура (slice.key, tokens.json) из tmp/ и .runtime/ — пересоздаются `shop_slice.py reset`;
  символических ссылок нет.
- Запущен свежий полный verify из чистой копии: .runtime/clean_report/work, `sh scripts/isolated_verify.sh runs/verify`,
  журнал .runtime/clean_report/clean_verify_report.log (~55 мин).
- Дальше: быстрые проверки rev11 (smoke/мутанты) в рабочем каталоге, затем report.md (раздел стадии report), status.json.
- Проверка контракта status.json (скрипт на стадии report): 38/38 ID, статусы допустимы, все evidence существуют,
  simplifications (264) = status.simplifications; validate_decomposition.py OK (20 компонентов).
- Черновик нового раздела report.md: .runtime/rev11_report_section.md (§R11.1–R11.8), ждёт итогов свежего verify.
- Замечено: в среде нет `pip` в PATH — на verify не влияет (requirements.txt пуст, шаг env PASS).

## Стадия report, продолжение (сессия 2026-11-11)
- Перечитаны brief.md, handoff.json (verification ok=true, checks=[], failures=[]), workflow-contract.json и все документы
  case/ целиком (5 основных + README + 23 разбора); хеши case/ совпадают с handoff.
- Свежий verify из чистой копии (запущен 2026-11-10 14:29, `sh scripts/isolated_verify.sh runs/verify`) завершился:
  PASS 3171.3 с, 109/109 шагов, 1024 теста, ключевые 17/17. Код после него не менялся.
  Доказательства скопированы в artifacts/checks/report_stage_verify/ (README, verify.json, key_requirements.json, logs/, rev11/).
- Новый scripts/report_stage_status.py (идемпотентный, офлайн): добавляет свежий verify в status.json, сверяет статусы
  ключевых требований с приёмкой, проверяет существование evidence. Итог: 29 implemented, 9 simplified, verify_ok=true.
- Контрольный прогон в сессии: `python3 scripts/rev11_verify.py --skip-rev10 --out runs/report_check_rev11` → PASS 123.7 с
  (artifacts/checks/report_stage_session2/).
- artifacts/report.md: раздел стадии report (R11.1–R11.8) из черновика + итог verify; версия integrate →
  artifacts/report-rev11-integrate.md; отчёт rev10 ниже без изменений. submission.json: добавлена заметка о verify report.
- Гигиена: удалены пересозданные синтетические slice.key/tokens.json (.runtime/clean_report/work/..., .runtime/shop_slice_smoke/);
  символических ссылок 0, секретоподобных файлов 0. Проверка контракта status.json — OK; validate_decomposition — OK.
- Стадия report завершена. Остатки — artifacts/report.md §R11.6–R11.7.
