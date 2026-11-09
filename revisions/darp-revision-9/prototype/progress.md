# darp-revision-9 — progress (стадия integrate, change attacker-isolation-boundary)

## Сделано
- Прочитаны brief, handoff, contract, openspec change, review, critique, coevolution; аудит кода attackers/runner/event_log.
- L1 воспроизведена (writer._emit.__self__ → EventLog).
- Артефакты rev8 заархивированы (artifacts/history_rev8, revision-8-*.md/json).
- artifacts/improvement-plan.md написан.
- Запущен baseline unittest до правок: artifacts/rev9/unittest_baseline_before.log.

## Следующий шаг
- Реализация darp_range/isolation (ipc, child shims, sandbox, probes, matrix, audit, invariance, provenance).

## Блок 2 (sandbox + probes)
- darp_range/isolation: ipc.py, child/ (шимы + attacker_child.py), sandbox.py (stage, BwrapLauncher, WeakLauncher, SandboxedAttacker, AttackerFactory), audit.py, probes.py, probe_world.py, probe_run.py, regression.py.
- runner: FlowContext.attacker_factory, Runner(attacker_factory=...), close() процесса после кампании.
- Регрессия D v1 seed 7 g0: identical (216 эпизодов, 30 кампаний), overhead 32 % на D (зафиксировать).
- Пробы v0 нашли 2 дефекта sandbox (stderr-файл хоста через /proc/1/fd; запись в корень sandbox) → исправлено (stderr pipe, --remount-ro /). Логи: artifacts/rev9/red/probes_v0_*.json, probes_v1_after_fix.json. Все 3 режима ok.
- Следующий шаг: матрица доступа, provenance/claim_level, invariance, скрипты и unittest.

## Блок 3 (матрица, provenance, invariance, тесты)
- configs/isolation/access_matrix.json (38 строк) + isolation/matrix.py; докстринг event_log исправлен.
- configs/isolation/provenance_registry.json (60 атак v1/holdout_v2/heldout_v3/trusted_v7 → implementation-informed), isolation/provenance.py (resolve, claim_level), evaluator/prompt_leak.py.
- isolation/invariance.py: 4 скрытых изменения × 3 брифа — payload идентичны; reply-контроль, leak-контроль ok.
- prespawn запасного процесса + отложенное завершение: overhead A stub 61% → 13–21%.
- tests/test_isolation_{matrix,sandbox,audit,provenance,probes}.py — все зелёные.
- Следующий шаг: scripts/isolation_verify.py (быстрая команда), mutate_rev9.py, полный unittest, verify из чистой копии, документы.

## Блок 4 (проверки и документы)
- isolation_verify PASS (12/12, ~70 с), mutate_rev9 12/12 убиты, unittest изолированно 970 OK.
- openspec tasks отмечены [x], validate valid; openspec-results.json, improvement-results.md, coevolution-progress.md, improvement-budget.json, simplifications (+9), case-update (раздел 9) записаны.
- Идёт полный verify из чистой копии tmp/clean_verify_rev9 (лог artifacts/rev9/clean_verify.log, старт 06:05Z).
- Следующий шаг: дождаться verify, обновить report.md, status.json, submission.json, production-readiness, evaluation-results; финальный isolation_verify (и под unshare -rn).

## Блок 5 (verify)
- Verify №1 из чистой копии: 116 PASS, 2 FAIL (revision7_checks, revision8_checks: необъявленное изменение darp_range/runner/flow.py, замороженного prereg rev7/rev8). Лог: artifacts/rev9/clean_verify_run1_fail.log.
- Ремонт: D9.1 в artifacts/preregistration-rev9-deviations.json; scripts/verify_e2e.py читает его (как D8.x). status.json обновлён (FR-1.1/1.3/1.4/1.18/1.20, NFR-2.2/2.11).
- Verify №2 запущен 06:52Z (artifacts/rev9/clean_verify.log).
- Следующий шаг: итог verify → report §3, status.verify_ok, submission; финальный isolation_verify (+ под unshare -rn).

## Блок 6 (стадия integrate, attempt 1 — ремонт verify №2)
- Verify №2: 117 PASS, 1 FAIL revision8_checks — KeyError 'requests_total': improvement-budget.json теперь бюджет rev9. Лог сохранён: artifacts/rev9/clean_verify_run2_fail.log.
- Ремонт D9.2: revision8_checks читает artifacts/revision-8-budget.json (как D8.6 для rev7); записано в preregistration-rev9-deviations.json. Проверено на выходе прогона №2: 10/10 подпроверок PASS.
- Финальный isolation_verify: PASS 12/12, 66.7 с (artifacts/rev9/isolation_verify_final.log).
- Verify №3 из свежей чистой копии tmp/clean_verify_rev9 запущен (лог artifacts/rev9/clean_verify.log).
- Следующий шаг: итог verify №3 → status.json, submission.json, report.
- Verify №3 (isolated_verify.sh, свежая чистая копия): PASS, exit 0, 2787.3 с, 118/118 шагов, unittest 970 OK (0 skip), ключевые 17/17. Код копии = рабочий каталог (diff -rq). Доказательства: artifacts/checks/revision9_verify/.
- Обновлены: status.json (verify_ok=true, доказательства revision9_verify у 17 ключевых; 29 implemented / 9 simplified), improvement-results.md (раздел «Полный verify»), report.md §3, submission.json notes.
- Незавершённое: живые прогоны в этой стадии не выполнялись (0 запросов к модели); остатки change перечислены в improvement-results.md.

## Стадия report (attempt 1) — блок 1: чтение ТЗ, гигиена снимка
- Прочитаны brief.md, handoff.json (step=report, verification.ok=true, 2026-11-09 12:47:53Z, checks=[]), workflow-contract.json, все документы case/ (5 основных + 23 методологических разбора).
- Найдено 6 символических ссылок в .runtime/isolation_probes/darp-inproc-*/ (подброшенные пробами ссылки на канарейку). Причина: _in_process в darp_range/isolation/probe_run.py не удалял свой каталог. Ремонт: try/finally + shutil.rmtree. Ссылки и устаревшие каталоги проб удалены.
- Проверки: scripts/isolation_verify.py → PASS 12/12, 65.4 с (artifacts/rev9/report_stage/isolation_verify.log); unittest test_isolation_* → 35 OK (artifacts/rev9/report_stage/unittest_isolation.log); после прогонов `find . -type l` = 0. probe_run.py не входит в замороженные файлы prereg rev1–rev9, мутации rev9 его не затрагивают.
- Секретоподобных файлов (.env*, ключи, сертификаты, secrets/) в каталоге не найдено.
- Следующий шаг: обновить artifacts/report.md (раздел о последней проверке контроллера, поломки, следующие шаги), status.json, simplifications.

## Стадия report — блок 2: документы, полный verify
- report.md: заголовок стадии, §4 (основание статусов rev9, FR-1.3 — односторонняя/не-по-умолчанию изоляция), §5 (246 упрощений), подпись verify rev8; копия до правок — artifacts/rev9/report_stage/report_before_report_stage.md.
- components/attackers.md: запись о ремонте очистки проб. Доказательства isolation_verify — artifacts/rev9/report_stage/isolation_verify/.
- status.json проверен: все evidence существуют, 29 implemented / 9 simplified, simplifications = simplifications.json (246, схема корректна).
- Запущен полный verify в свежей чистой копии tmp/clean_verify_report9 (isolated_verify.sh), лог artifacts/rev9/report_stage/clean_verify.log.
- Следующий шаг: итог verify → report §3, §6, §8; при PASS — копия доказательств в artifacts/checks/report9_verify/.

## Стадия report — блок 3: итог
- Полный verify в чистой копии tmp/clean_verify_report9: PASS, exit 0, 2801.2 с, 118/118, unittest 970 OK, ключевые 17/17. Код копии = рабочий каталог. Доказательства: artifacts/checks/report9_verify/.
- report.md §3 (handoff.verification 2026-11-09 12:47:53Z ok, новый verify), §6 (ремонт), §8 (гигиена) обновлены. status.json статусов не менял (29/9), симлинков 0.
- Незавершённое (не в рамках стадии): изоляция защитника, sandbox по умолчанию, живые гвардрейлы, blind-набор, статья.
