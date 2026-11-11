## 1. Tasks

- [x] revision-scoped-checks Проверки закрытых ревизий — исторические (хеш предрегистрации + записанные артефакты + поведенческие проверки, дрейф кода информационный), бюджеты revision-N-budget.json, быстрая команда scripts/rev10_verify.py; правка комментария в runner/flow.py не роняет verify, подмена предрегистрации rev8 и мутант исхода rev7/rev8 роняют
- [x] broker-authoritative-record Брокер собирает AttemptRecord из своей копии канала, ребёнок шлёт только self_report; 6 видов порчи → ipc_protocol_error, регрессия sandbox ↔ in-process побайтно (D v1 seeds 7/8/9, A stub ≥3×3)
- [x] broker-side-isolation-evidence Пробы выполняет код брокера под тем же argv, брокер наблюдает sid/NSpid/гибель ребёнка, новые пробы PID-count, /proc/*/environ, CapEff; мутанты MX2, MX4, MX6 убиты, MX3 убит или задокументирован как эквивалентный
- [x] claim-level-in-manifest claim_level в manifest.json каждого прогона (по умолчанию in_process_research), живой A по умолчанию в sandbox (без bwrap и флага — ошибка при 0 запросов), поле enforcement в матрице, defender→attacker_memory = convention с записанной gc-пробой
- [x] v2r-primary-table Главный прогон verify и таблица L* на --foundation v2r (seeds 7/8/9), v1 — legacy-шаг побайтно прежний; приёмка не требует ΔV>0, мутанты «main на v1» и «неверный статус» роняют verify

## 2. Порядок и проверки (текст, не задачи)

Порядок: revision-scoped-checks → broker-authoritative-record → broker-side-isolation-evidence →
claim-level-in-manifest → v2r-primary-table → scripts/rev10_verify.py → один полный verify из чистой
копии (scripts/isolated_verify.sh).

RED для каждой задачи фиксируется до правки, логи сохраняются в artifacts/rev10/red/:
- правка комментария flow.py роняет revision7/8_checks;
- враждебный ребёнок (поддельный record, cost=1e9, чужой episode_id, неверные turns) принят или даёт
  исключение эпизода;
- MX2/MX4/MX6 выживают;
- manifest.json без claim_level;
- главный e2e на v1.

Запросы к Qwen: 0. Живой путь A проверяется только фейковым транспортом.

Замечание к revision-scoped-checks: в rev9 общий `improvement-budget.json` содержит учёт rev9. Его
неизменённая копия сохраняется как `artifacts/revision-9-budget.json` (sha256 в журнале), а
`revision9_checks` читает только её.
