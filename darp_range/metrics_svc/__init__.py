"""metrics-svc — расчёт финальных метрик по телеметрии как отдельный сервис (issue #33).

Телеметрия остаётся в СС; сервис получает её потоком по 127.0.0.1 и считает метрики по
канонным формулам `darp_range.metrics` без изменений (`E_L`/`E_A`, аналитика очереди
`λ_Q = λ_L·E_L + λ_A·E_A`, ΔNet/L*, случаи У1–У4, `queue_unstable`), плюс гейты отчёта
(G-tau, honest accounting) и независимый пересчёт L*. Код измерения СС (runner, evaluator,
executor, агент, гварды) сервис не импортирует — переносится на полигон без переделки.

Контракт (сигналы начала/завершения расчёта, поток телеметрии, отчёт):

    POST /runs                       начало: run_id, manifest, run (сводка), source
    POST /runs/<id>/results          результаты конфигурации (`results/<config>.json`)
    POST /runs/<id>/log              часть журнала потока (строки JSONL), path внутри logs/
    POST /runs/<id>/finish           конец: reference, l_viol → файлы отчёта
    GET  /health

- `gates` — G-tau, honest accounting, `recompute_lstar`;
- `server`, `__main__` (`python3 -m darp_range.metrics_svc --workdir DIR --ready FILE`);
- `client` — адаптер СС: запуск сервиса, поток телеметрии из каталога прогона, отчёт.
"""
