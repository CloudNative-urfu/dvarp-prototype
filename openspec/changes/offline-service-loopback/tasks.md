# Tasks — issue #61

- [x] `contour.no_network(allow=...)`: allowlist собственных сервисов (только loopback,
  порт из ready.json), фиксация каждого разрешённого хода, сводно в contour.json.
- [x] `run_offline`: metrics-svc до блока запрета; прогоны и расчёт внутри блока
  по allowlist; `metrics_svc=False` отклоняется.
- [x] `run_live`: расчёт только через metrics-svc; `--no-metrics-svc` отклоняется;
  CLI-флаг по умолчанию включён.
- [x] Мутационная проверка контура обновлена под новую форму блока запрета.
- [x] Тесты: allowlist (pass/deny/фиксация/агрегация), отказ in-process расчёта,
  contour.json с allowed, живой конвейер через сервис.
- [x] Спека capability `metrics-service` (MODIFIED/ADDED), мануал `metrics-svc.md`,
  `run-lifecycle.md` §7.
