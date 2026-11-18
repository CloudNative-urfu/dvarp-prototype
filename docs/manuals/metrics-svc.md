# Мануал: расчёт метрик как сервис (metrics-svc)

> **Статус актуальности:** ветка `refactor/33-metrics-svc` (issue #33, поверх #19), 2026-11-16.
> **Устареет при** подключении полигона (адрес сервиса) и прогоне-миксе dvarp#20.
> Спека — `openspec/changes/metrics-svc/`.

Как СС отдаёт телеметрию прогона сервису метрик и что сервис возвращает. Формулы — те же
`darp_range.metrics` (см. [run-lifecycle.md](run-lifecycle.md), разделы 4–6).

## 1. Контракт

- Сигнал начала `POST /runs` {run_id, manifest, run, source} — `metrics_svc/server.py:125-136`;
  повторный run_id — 409, имена проверяются (`_name`, server.py:24-27).
- Поток телеметрии: `POST /runs/<id>/results` {config, doc} — результаты конфигурации
  (эпизоды с вердиктами оценщика, эскалациями, затратами); `POST /runs/<id>/log` {path, lines} —
  журналы потоков частями (per-turn сигналы гвардов, решения политики, затраты);
  маршруты — server.py:91-123.
- Сигнал завершения `POST /runs/<id>/finish` {reference, l_viol} — server.py:138-170: проверка
  хеш-цепочек всех журналов (`verify_chain`, :142; иначе 422 `telemetry_invalid`) →
  `metrics.load_run` + `compute` + `write` → гейты → ответ `{files: имя → текст}`.

## 2. Гейты отчёта (`metrics_svc/gates.py`)

- Независимый L*: `recompute_lstar` (:29-47) по §9.2 из составляющих metrics.json и
  `lstar_check` (:50-93) — сверка с отчётным L* и правила статусов; приёмка
  `report_cli/acceptance.py` (FR-1.24) импортирует их отсюда.
- G-tau: `interrupted_legal` (:106-117) — легальные эпизоды потоков `base` с действием политики
  `isolate`/`escalate`/`terminate`; `g_tau` (:120-136) — доля, интервал Уилсона 95 %, порог 5 %
  (`TAU_MAX`, :23); не прошедшая конфигурация помечается (`marked`), из таблиц не исключается.
- Honest accounting: `honest_accounting` (:139-…) — проверки раздельного учёта потоков,
  обе стороны в отчёте, нет расхода атакующего в базовых потоках, строка транспорта
  defender-svc (`defender_borne_transport`, #19) полная и отдельная.
- Сводка: `gates.json` / `gates.md` (server.py:37-88).

## 3. Сторона СС

- Адаптер `metrics_svc/client.py`: `MetricsService` (:23-81) запускает сервис через
  `svckit.ServiceProcess` (`svckit/proc.py`, `PYTHONHASHSEED=0`), шлёт каталог прогона
  (`compute`, :54-81), `write_report` (:83-…) кладёт файлы в `seed-N/metrics`.
- `report_cli live --metrics-svc` передаёт телеметрию локальному сервису. В `report_cli offline`
  метрики считаются в процессе; TCP/UDP запрещены даже для `127.0.0.1`, исключений по портам нет.
  Для отдельного запуска defender-svc используется `python3 -m darp_range.runner svc`.
- Смоук «прибор исправен» до отправки: `instrument_smoke` (pipeline.py:153-172) — журналы
  целы и закрыты, у каждого эпизода вердикт оценщика с диффом состояния БД.

## 4. Проверки

- `tests/test_metrics_svc.py` (в `dev_smoke`): байт-в-байт с расчётом в процессе; гейты;
  отказ при разорванной цепочке; смоук; сервис и `metrics` (кроме CLI-демо) не импортируют
  код измерения СС.
