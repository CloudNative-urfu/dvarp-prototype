# Прогресс программы коэволюции DARP (исполнитель rev10, 2026-11-10)

Change `trusted-trace-and-v2r-table` реализован (`artifacts/openspec-results.json`: 5/5 implemented по оценке
автора; финальную оценку даёт критик). Сводка проверок: `artifacts/rev10/fast/summary.md`. Живых запросов: 0.
Версия rev9 этого файла — `artifacts/history_rev9/coevolution-progress.md`.

| Пункт | Статус | Код / доказательство | Ближайшая зависимость |
|---|---|---|---|
| A1 модель угроз, матрица доступа | implemented для атакующего; у каждой строки `enforcement=os\|convention`, у каждой deny+os — своя OS-проба с канарейкой; defender→attacker_memory — `convention` с записанной утечкой | `configs/isolation/access_matrix.json`, `darp_range/isolation/matrix.py`, `tests/test_rev10_claim_matrix.py`, `artifacts/rev10/fast/defender_gc_probe.json` | OS-изоляция защитника |
| A2 разные процессы, OS-негативные проверки | partial: атакующий — implemented; доказательства собирает процесс брокера (не атакующий) под тем же argv bwrap: 23 отрицательные пробы + 4 наблюдения хоста (sid, NSpid, argv, die-with-parent), MX2/MX4/MX6 убиты, MX3 эквивалентен; защитник — в процессе брокера; verify/offline по умолчанию in-process, живой A по умолчанию в bwrap | `darp_range/isolation/{broker_probes,sandbox}.py`, `probe_entry/probe_main.py`, `artifacts/rev10/fast/probes/probes.md`, `mutations/mutations.json` | процесс защиты; sandbox по умолчанию в verify |
| A3 аудит payload, канарейки, инвариантность; целостность трассы | implemented: запись попытки строит брокер из своей копии канала, ребёнок шлёт только self_report; 7 видов порчи → `ipc_protocol_error`; затраты атакующего пишет только ChatModel брокера | `darp_range/isolation/{ipc,sandbox}.py`, `artifacts/rev10/fast/hostile_child.json`, `regression_sandbox_vs_in_process.json` | живой срез A через sandbox с журналом payload |
| A4 изоляция создания атак, provenance, уровень притязания | partial: `claim_level` с фактами в manifest.json каждого прогона (схема /2); гейт заявлений; attack-only рабочее место и blind-набор критика — нет | `darp_range/repro/manifest.py`, `darp_range/isolation/provenance.py` | blind held-out критика |
| A5 наблюдения клиента, утечка промпта как исход | partial (без изменений rev10) | `darp_range/evaluator/prompt_leak.py` | B2 |
| B1–B5 раздельный опыт, strict/research, память | pending | память атакующего — только в его процессе; believed_success — самоотчёт с меткой | A2 (защитник), хранилища |
| C1–C6 лига версий, закрытый набор, метрики | partial для C-метрик: главная таблица L* — на рекомендуемой основе v2r (seeds 7/8/9), legacy v1 отдельно; на заглушке все конфигурации `unresolved`; проверки закрытых ревизий — исторический контракт (drift кода информационный) | `scripts/verify_e2e.py`, `scripts/historical.py`, `artifacts/checks/revision10_verify/` | B; живой L* на v2r |

## Что это даёт и чего не даёт
- Даёт: трасса попытки и учёт затрат атакующего не зависят от проверяемой стороны; доказательство изоляции
  чувствительно к флагам bwrap и не опирается на самоотчёт процесса атакующего; у каждой цифры отчёта есть уровень
  притязания в манифесте; правка общего кода больше не роняет проверки закрытых ревизий.
- Не даёт: изоляции защитника (известная утечка записана), strict-black-box заявлений, опыта между раундами,
  живого измерения эффекта гвардрейлов на v2r.

## Ограничения
Один Qwen 9B в обеих ролях; изоляция процессов не делает роли независимыми семействами моделей.
Режим strict-black-box остаётся неподтверждённым.
