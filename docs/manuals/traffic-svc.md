# Мануал: сервис легального трафика

> Срез: `develop` e3a3ef5 + ветка `Issue-#36`, 2026-11-19.
> Актуализировать при изменении TrafficPort, формата снимка/манифеста или канала.

1. Запускай `python -m darp_range.runner svc --configs g0,g_proc --split dev
   --blocks 1 --out runs/traffic`. Фабрика — `SvcTraffic`
   (`darp_range/runner/__main__.py:84`), legacy Runner сохраняет CorpusTraffic.
2. Оркестратор один раз на config seed читает CSV каталога и заметок, создаёт
   EpisodeStateManager и кеширует менеджер: `darp_range/runner/svc.py:358`.
   Конфигурации с seed_label=main получают один физический снимок; намеренный
   контроль seed2 — отдельный. Поля приглашения — FlowInvite, config/seed,
   budget, snapshot_id и snapshot_hash (`darp_range/runner/ports.py:85`).
3. Фабрика пишет launch.json из существующего снимка и манифеста, запускает
   процесс через ServiceProcess (`darp_range/runner/traffic.py:24`). Каталог
   процесса уникален, поэтому старый ready.json не принимается за новый.
4. До bind порта проверь снимок и манифест, включая ссылки account/archetype →
   order (`darp_range/traffic_svc/server.py:19`). Сервис не создаёт базу.
   `/flow` проверяет id+hash приглашения и применяет его бюджет
   (`darp_range/traffic_svc/server.py:40`). Расписание строится общим ядром
   `darp_range/runner/legal_traffic.py:28`: формулы arrivals/производные seed,
   порядок и base/attacked остаются прежними. Сервисный прогон исполняет legal.
5. Перед эпизодом `prepare` восстанавливает снимок с проверкой хеша;
   оркестратор вызывает bind (`darp_range/runner/flow.py:251`). `/bind`
   дополнительно проверяет хеш восстановленной БД и неизменность control/index,
   читает её только readonly, выбирает реальные аккаунты/заказы из манифеста.
   Fixture и GT возвращаются только harness; fixture разворачивает оркестратор
   до открытия defender-сессии (`darp_range/runner/flow.py:280`).
6. Клиент — RemoteUser (`darp_range/runner/traffic.py:149`). В сервис передаются
   только поля Observation; обратно — строка. Не передавай клиенту EventLog,
   guard-решения или GT. ResponsiveUser получает только реплики и факты клиента,
   как эталонный in-process путь. `on_close`, `summary` и `end_reason` передают
   окончание эпизода; финальные метрики сервис не рассчитывает.
7. Один точный повтор транспортного вызова безопасен: Handler кеширует
   последний ответ под lock (`darp_range/traffic_svc/server.py:118`); изменённое
   тело с тем же req отвергается. Два сбоя либо не-200 → остановка процесса и
   исключение TrafficServiceError. Повторы видны в requests.jsonl процесса;
   транспорт не входит в сравнение паритета.
8. В конце блока `run_flow` вызывает close, после чего процесс остановлен.
   Следующий блок переоткрывает сервис с тем же приглашением. Закрытие также
   выполняется при отказе defender-svc или исключении в оркестраторе
   (`darp_range/runner/svc.py:350`, `darp_range/runner/flow.py:586`).

Проверка: `tests/test_traffic_svc.py:35` — паритет расписания/бюджета, отклонение
чужого id/hash и изменённого эпизода, реальные строки и неизменность БД после
bind, текст/summary, повтор без двойного шага, отказ по фиктивному манифесту,
падение процесса и два блока/две конфигурации с общим CSV-снимком. Сравнение с
обоими сервисами: `tests/test_traffic_svc.py:189`; регрессия defender-сервиса —
`tests/test_defender_svc.py:129`. Общий быстрый набор включает оба модуля.

Особенность defender-сервиса: SQLite-соединение нельзя использовать из разных
HTTP-потоков даже под lock. Все операции эпизода выполняются одним worker
(`darp_range/defender_svc/server.py:45`); это сохраняет thread affinity и
single-writer без изменения defense-каталогов.
