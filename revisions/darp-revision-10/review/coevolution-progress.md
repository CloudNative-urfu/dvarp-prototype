# Прогресс программы коэволюции DARP — оценка критика rev10 (2026-11-10)

Источник: собственные прогоны в `tmp/` (логи в `artifacts/logs/`), не отчёт автора. Статусы задач change —
`artifacts/openspec-review.json`.

| Пункт | Автор | Критик | Доказательство критика | Ближайшая зависимость |
|---|---|---|---|---|
| A1 модель угроз, матрица доступа | implemented (атакующий) | implemented для атакующего; defender→attacker_memory честно `convention` | `logs/matrix_rows.txt` (27 deny/os строк, 27 разных проб) | изоляция защитника |
| A2 разные процессы, OS-негативы | partial | partial: MX2/4/6/7/9 убиты, MX3/MX8 эквивалентны; защитник в процессе брокера; verify, report_cli live и Runner по умолчанию in-process | `logs/mx_mutants.log`, `logs/runner_live_a_default.log` | sandbox по умолчанию для всех live-A путей; процесс защиты |
| A3 целостность трассы, payload | implemented | implemented для трассы; остатки: самоотчёт cpu/latency атакующего до 1 ч на реплику, свободные поля `data` событий, нет тайм-аута IPC (зависание брокера), неограниченный буфер stderr | `logs/hostile2.log`, `logs/hostile2_hang.log` | тайм-аут IPC → `ipc_protocol_error`; брокер сам меряет время композиции |
| A4 provenance, claim_level | partial | partial: claim_level в манифесте есть; живой A через bwrap работает с Qwen (7 запросов, `isolated_implementation_informed`); blind-набора нет | `critic_live_A_sandbox/manifest.json` | blind held-out критика |
| A5 наблюдения клиента | partial | partial (не менялось) | — | B2 |
| B1–B5 опыт/память | pending | pending | — | A2 (защитник) |
| C1–C6 лига, метрики | partial | partial: главная таблица на v2r, но V=0 во всех 19 конфигурациях × 3 seed → все `unresolved`, L* не определён ни для одной | `logs/full_verify.log`, `candidate/artifacts/checks/revision10_verify/e2e/report.md` | атаки, ломающие v2r-g0; верхняя граница ΔV → нижняя граница L* |

Следующий уровень программы (B) не готов: нет изоляции защитника и не закрыт тайм-аут IPC.
Strict-black-box остаётся неподтверждённым.
