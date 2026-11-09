# Прогресс программы коэволюции DARP (исполнитель rev9, 2026-11-09)

Change `attacker-isolation-boundary` реализован (`artifacts/openspec-results.json`: 5/5 implemented по оценке автора;
финальную оценку даёт критик). Сводка проверок: `artifacts/rev9/isolation/summary.md`. Живых запросов: 0.

| Пункт | Статус | Код / доказательство | Ближайшая зависимость |
|---|---|---|---|
| A1 модель угроз, матрица доступа | implemented для атакующего; defender/evaluator — строки по соглашению | `configs/isolation/access_matrix.json`, `darp_range/isolation/matrix.py`, `tests/test_isolation_matrix.py` | OS-изоляция защитника |
| A2 разные процессы, OS-негативные проверки | partial: атакующий — implemented (bwrap + брокер + 19 проб, sandbox 19/19 DENY, контроли in-process и weak дают утечки); защитник — в процессе брокера; sandbox включается фабрикой, по умолчанию in-process | `darp_range/isolation/{sandbox,probes,probe_run}.py`, `artifacts/rev9/isolation/probes/probes.md`, `regression_mode_{d,a}.json` | процесс защиты; sandbox по умолчанию |
| A3 аудит payload, канарейки, инвариантность | implemented (на фиксированных ответах агента) | `darp_range/isolation/{audit,invariance}.py`, `artifacts/rev9/isolation/invariance.json` | живой срез A через sandbox с журналом payload |
| A4 изоляция создания атак, provenance | partial: реестр + гейт заявлений implemented; attack-only рабочее место автора сценариев и blind-набор критика — нет (attack-cards без новых атак) | `configs/isolation/provenance_registry.json`, `darp_range/isolation/provenance.py` | blind held-out критика |
| A5 наблюдения клиента, утечка промпта как исход | partial: исход `system_prompt_leak_via_reply` implemented (канарейка, без фильтрации); read-only статус заявки — нет | `darp_range/evaluator/prompt_leak.py` | B2 |
| B1–B5 раздельный опыт, strict/research, память | pending | память атакующего — только в процессе атакующего (брокер её не держит, тест `test_broker_holds_no_attacker_memory`), не переживает процесс | A2 (защитник), хранилища |
| C1–C6 лига версий, закрытый набор, метрики | pending | заморозка конфигураций (lock v6), оценщик по БД, claim_level прогона | B |

## Что это даёт и чего не даёт
- Даёт: прогон, в котором атакующий в sandbox, OS-пробы прошли и брифы зарегистрированы, получает вычисляемый
  `claim_level`; legacy-корпуса дают максимум `isolated_implementation_informed`.
- Не даёт: заявления strict-black-box на реальных данных (нет blind-брифов), изоляции защитника, опыта между раундами.

## Остатки критики rev8, перенесённые явно
п.1 v2r в главной таблице L*; п.2 живые гвардрейлы; п.3 правило дефектов пар до запуска; п.5 быстрый verify ≤10 мин;
п.6 boundary-1 помечен legacy в матрице доступа (в таблицах отчёта — прежняя пометка); п.7 слепой held-out.

## Ограничения
Один Qwen 9B в обеих ролях; изоляция процессов не делает роли независимыми семействами моделей.
