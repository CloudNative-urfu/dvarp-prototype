# traffic-svc

Реализация `TrafficPort` (#36) поверх общего ядра `runner/legal_traffic.py`.
Только stdlib; HTTP через svckit на `127.0.0.1`. Защищаемая сторона получает
реплики через оркестратор и TextChannel; ground truth остаётся у оценщика.

Обычный запуск обоих сервисов:

```sh
python -m darp_range.runner svc --configs g0,g_proc --split dev --blocks 1 --out runs/traffic
```

Прямой запуск для диагностики:

```sh
python -m darp_range.traffic_svc --launch launch.json --ready ready.json --port 0
```

Launch JSON: `snapshot_path` (с сопроводительным `.json`), `snapshot_id`,
`manifest` (формат #38), `plan` (полный `dataclasses.asdict(RunPlan)`), `entries`
(записи корпуса в исходном порядке). Сервис не генерирует БД: он проверяет
снимок и манифест до ready.json. Некорректный снимок → отказ до открытия порта.
В сервисном прогоне снимок создаёт оркестратор из CSV `state_store/seed/`.

Каждый POST содержит возрастающий `req`. Повтор последнего запроса с тем же
телом идемпотентен; другое тело с тем же номером → HTTP 409. Транспортный
адаптер делает один точный повтор; повторный сбой/не-200 прерывает прогон.
Повтор виден по `req` в `requests.jsonl` процесса. Health: `GET /health`.

| POST | Тело помимо req | Результат |
| --- | --- | --- |
| `/flow` | `invite`: config, seed, budget (n_legal, blocks, mean_gap), snapshot_id, snapshot_hash | `blocks` в формате BlockSchedule.to_json |
| `/bind` | entry, entry_index, db_path, episode_id, traffic="legal" | actor_id, ticket_id, order_id, fixture, truth, bound, handle |
| `/client/next` | handle, observation (только поля Observation) | text (str или null) |
| `/client/close` | handle, observation | {} |
| `/client/summary` | handle | summary, end_reason |
| `/close` | — | end_reason="flow_completed" |

`db_path` до bind — восстановленный снимок без фикстуры. Хеш обязан совпадать.
Чтение только `readonly=True`; фикстуру пишет оркестратор. Идентификаторы
аккаунтов/заказов берутся из манифеста и проверяются по реальным строкам.
После close эпизоды отвергаются; адаптер останавливает процесс. Следующий
блок запускает новый процесс с тем же приглашением и тем же снимком.

Проверки: `python -m unittest tests.test_traffic_svc tests.test_defender_svc`;
общий набор — `python scripts/dev_smoke.py`. Подробный трейс:
[`docs/manuals/traffic-svc.md`](../../docs/manuals/traffic-svc.md).
