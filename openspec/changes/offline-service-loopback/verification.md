# Verification — 2026-11-20

Base: `develop` `af4f3d8`; branch: `feat/61-offline-service-loopback`.
Python 3.14.7, Nix (Linux).

| Check | Result |
| --- | --- |
| `pre-commit` (ruff, ruff-format) по изменённым файлам | passed |
| `python3 -m unittest tests.test_report_cli` (28 тестов, вкл. новые: allowlist pass/deny/strict, отказ in-process расчёта, `contour.json` с allowed) | OK, 71 s |
| `python3 scripts/dev_smoke.py` (12 модулей, 291 тест) | OK, 101.8 s (budget 300 s) |
| `python3 scripts/mutate_report_cli.py` | **25/25 мутантов убито** (включая обновлённые под reflow шаблоны «офлайн без запрета сети», «у каждой модели свой счётчик», «дефекты не валят самопроверку», «статья не помечена вне рамок», «summary: L* low = base») |

## Ручная проверка сценария ТЗ

- офлайн-прогон (`run_offline`): metrics-svc стартует до блока запрета сети; прогоны
  всех seed и расчёт метрик каждого seed идут **внутри** блока; соединения с сервисом
  проходят по allowlist (порт из ready.json), каждый ход фиксируется; в
  `contour.json` → `network.allowed` = сводно по (service, host, port) с числом
  соединений; попытки чего-либо иного — `NetworkBlocked` + запись в `attempts`;
- расчёт в процессе отклонён: `run_offline(..., metrics_svc=False)` и
  `run_live(..., metrics_svc=False)` → `PipelineError` до прогона; CLI
  `--metrics-svc` включён по умолчанию, `--no-metrics-svc` отклоняется;
- не-loopback с разрешённым портом и чужие порты — запрещены (тест
  `test_no_network_allowlist_strict`, включая `connect_ex` и UDP `sendto`);
- байт-в-байт парити «сервис == в-процессе» не тронут (#56, `tests/test_metrics_svc.py`).

Полный `scripts/verify.py` — отдельно от этого PR (юнит-набор и смоук покрывают
затронутые инварианты; независимый пересчёт в процессе остаётся шагом verify).

## Мутационная проверка (полный лог прогона)

25/25 убито: все мутанты контура (запрет TCP/UDP/AF_UNIX, восстановление сокетов,
импорты, гвардрейлы NFR-2.4/2.5, бюджет живого режима, конвейер и отчёт NFR-2.14)
обнаружены тестами. Обновлены 5 шаблонов, отставших от ruff-reflow или старой формы
блока запрета (в т.ч. «офлайн без запрета сети» — под `no_network(allow=...)`).
