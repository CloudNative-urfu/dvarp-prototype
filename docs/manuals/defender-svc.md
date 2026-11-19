# Мануал: защищаемая сторона как сервис (defender-svc)

> **Статус актуальности:** ветка `refactor/19-svc-decomposition` (issue #19), 2026-11-16.
> **Устареет при** traffic-svc (#36: адаптер `legal_traffic` заменяется сервисом) и
> metrics-svc (#33: агрегаты уходят из потока). Спека — `openspec/changes/svc-decomposition/`.

Как оркестратор гоняет эпизоды, когда защищаемая сторона — отдельный процесс, и почему
результат совпадает с in-process путём.

## 1. Порты оркестратора

- Контракты — `darp_range/runner/ports.py`: `EpisodeInvite` (:34-52), `ToolPort` (:57-60),
  `DefenderSession` (:63-74), `DefenderPort` (:77-81), `FlowInvite` (:85-100),
  `BoundEpisode` (:105-115), `TrafficPort` (:118-139).
- Ядро потока знает только их: `FlowContext` берёт фабрики портов (runner/flow.py:177-191);
  `_EpisodeRun` привязывает обращение через `TrafficPort.bind` (flow.py:220-222),
  развёртывает фикстуру (:223-227), открывает эпизод защищаемой стороны `open(EpisodeInvite)`
  (:229-234), канал получает `dsess.defender()` и `dsess` как `ToolPort` (:241-255).
- Адаптеры по умолчанию (in-process эталон) регистрирует корень сборки
  `runner/__init__.py:37`: `inproc.InProcessDefender`, `legal_traffic.CorpusTraffic`,
  `attack.run_attack_slot` (атакующий слот — только legacy `Runner`).
- Атакующий слот без обработчика — `RunnerError` (flow.py:465-476): сервисный режим гоняет
  только легальный трафик.

## 2. Один код защиты в двух режимах

- `defender_svc/core.py`: `DefenderFlow` (:19-46) — модель агента, `ProcessLedger` потока,
  граница исполнителя, `FreezeGuard`; `DefenderEpisode` (:49-97) — `Executor` на БД эпизода
  (:62-68, единственный писатель), `assemble_defender` + `FreezeGuard.check` (:72-87),
  история обращения (`record`), `release`/`close`.
- In-process: `runner/inproc.py:18-35` строит тот же `DefenderFlow` в процессе оркестратора.
- Сервис: `defender_svc/server.py` — маршруты (:60-145) поверх того же ядра.

## 3. Протокол сырых записей (журнал пишет только оркестратор)

1. Каждый вызов несёт `req` и `events` — дельту журнала оркестратора с прошлого вызова
   (runner/svc.py:245-275, `SvcDefenderPort.post`).
2. Сервис докладывает дельту в зеркало журнала потока (`defender_svc/mirror.py:35-47`,
   сверка `seq`), компоненты защиты пишут в зеркало обычным `EventLog._append` (те же
   проверки схемы), ответ несёт только новые события (`drain`, mirror.py:49-53).
3. Оркестратор дописывает их своими писателями в том же порядке и сверяет `seq`
   (svc.py:257-266); хеш-цепочку и `wall_ms` считает он сам.
4. Исключение защиты — ответ 200 с `raised` (server.py:137-145); клиент поднимает
   исключение с тем же именем типа (svc.py:66-70), поэтому ошибка эпизода совпадает с in-process.

## 4. Жизненный цикл процесса

- `SvcRunner.run_config` (runner/svc.py:372-456): seed конфигурации → снимок →
  `FlowInvite` (:379-389) → `TrafficPort.schedule` (:391) → `DefenderService` (:393-410) →
  поток каждого блока `run_flow` с `SvcDefenderPort` (:413-434) → итоги ячейки.
- `DefenderService.start` (svc.py:99-135): стадирование `configs/*.json` + lock (:73-77),
  `python3 -m darp_range.defender_svc`, ожидание `ready.json` (порт 0), реестр `svc/pids.json`;
  остановка — SIGTERM, затем kill (:147-158).
- Сервис до открытия порта: `load_frozen` + `FreezeGuard` (defender_svc/__main__.py:36-45);
  дрейф → код 3 без `ready.json` → `SvcRefused`, ячейка `refused` (svc.py:400-408).

## 5. Fail-closed и честный учёт

- Обрыв/тайм-аут: один повтор с тем же `req` (svc.py:172-199); сервис отвечает
  сохранённым ответом (server.py:78-86); событие `runner.svc_retry` после событий сервиса
  (svc.py:267-271).
- Второй сбой, не-200, смерть процесса: `SvcError` → сессия помечена (svc.py:280-319),
  эпизод — `svc-error` (flow.py:268-273); дальнейшие вызовы потока не делаются (`broken`).
- Транспорт: `defender_borne_transport` (svc.py:202-213) — запросы, повторы, байты,
  время хопа без обработки в сервисе; в затраты потока отдельной строкой (flow.py:490-494).

## 6. Паритет и проверки

- `tests/test_defender_svc.py`: паритет легальных потоков (`g_layered_term`, `g_proc`,
  dev-срез) и решений гвардов на варианте с атаками (`g_in_iso`); повтор; `svc-error`;
  отказ при дрейфе; `SvcRunner`; архитектурный импорт-тест (ядро оркестратора — только
  контракты; defender-svc — без `attackers`/`isolation`/`corpus`/`evaluator`/`metrics`,
  статически и по `/health` живого сервиса). Входит в `scripts/dev_smoke.py`.
- Запуск: `python3 -m darp_range.runner svc --out DIR [--configs g0,g_proc] [--agent-mode live]`.
## Границы импортов (#60)

`darp_range/configs/__init__.py` загружает сборку защиты по запросу. Импорт
`ConfigSpec` и `load_frozen` не загружает исполнитель; сам вызов `load_frozen`
по-прежнему проверяет схемы и отпечатки. Идентификаторы для измерительной стороны
опубликованы в `darp_range/boundary_contract.py:4`; при новой версии границы их
нужно сверить с замороженным исполнителем. Расписание строит адаптер
`darp_range/runner/legal_traffic.py`; формат плана и seed не изменились.
