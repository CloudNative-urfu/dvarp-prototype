## Why

Ревизия 9 вынесла атакующего в процесс под bubblewrap. Критик rev9 (candidate/critique.md §3) показал,
что трасса и доказательство изоляции остаются самоотчётом проверяемой стороны:
- брокер принимает поддельный `AttemptRecord`;
- порча IPC даёт ошибку эпизода вместо `ipc_protocol_error`;
- пробы выполняет сам атакующий, и тесты не чувствительны к pid/ipc-namespace, `--die-with-parent`,
  `--new-session`;
- `claim_level` не попадает в `manifest.json`.

Кроме того, каждая ревизия ломает замороженные проверки прошлых ревизий (D6.4…D9.2, три полных
verify в rev9). Главная таблица `L*` в verify строится на legacy-основе v1, а не на рекомендуемой
владельцу v2r.

## What Changes

- Брокер становится единственным автором `AttemptRecord`. От процесса атакующего принимается только
  помеченный `self_report` (tactics, believed_success). Любая ошибка разбора `event`/`record` →
  `ipc_protocol_error`. Затраты модели атакующего считает только брокер.
- Пробы изоляции выполняет код брокера в отдельном пробном процессе под тем же argv. Брокер сам
  наблюдает sid, NSpid и гибель ребёнка. Новые пробы: PID-count, `/proc/*/environ`, `CapEff`.
  `self_check` перестаёт быть доказательством.
- `claim_level` записывается в `manifest.json` каждого прогона. Живой режим A по умолчанию идёт через
  sandbox. В матрице доступа появляется обязательное поле `enforcement`, строка
  defender→attacker_memory становится `convention`.
- Проверки закрытых ревизий переходят на исторический контракт: хеш предрегистрации + неизменность
  записанных артефактов + поведенческие проверки; дрейф текущего кода — информационный. Бюджеты —
  `revision-N-budget.json`. Добавляется быстрая команда change.
- Главный сквозной прогон verify и таблица `L*` строятся на `--foundation v2r`; v1 сохраняется как
  помеченная legacy-таблица. Приёмка не требует знака эффекта.

## Capabilities

### New Capabilities
- `attacker-trace-integrity`: запись попытки атакующего, собранная брокером; закрытый контракт `self_report`; порча IPC → нарушение протокола.
- `isolation-evidence`: пробы изоляции под контролем брокера, чувствительные к флагам bwrap.
- `run-claim-level`: уровень притязания в манифесте каждого прогона; поле enforcement матрицы доступа.
- `revision-check-scoping`: исторические проверки закрытых ревизий и пофайловый учёт бюджета.
- `primary-decision-table`: главная таблица `L*` на основе v2r с legacy-таблицей v1.

### Modified Capabilities
- нет (спецификации rev9 `attacker-isolation`, `attack-provenance` не архивированы в общий набор; новые требования добавляются отдельными capability)

## Impact

- Код: `darp_range/isolation/{sandbox.py, ipc.py, probes.py, probe_run.py, matrix.py}`, `isolation/child/attacker_child.py`,
  `repro/manifest.py`, `runner/orchestrator.py`, `scripts/{verify_e2e.py, live_mode_a_v.py}`, новый `scripts/rev10_verify.py`,
  `configs/isolation/access_matrix.json`.
- Не затрагивается: серверные правила, оценщик, легальный клиент, lock конфигураций, корпуса, предрегистрации прошлых ревизий.
- Запросы к Qwen: 0. Нужны bubblewrap и user namespaces (есть у автора и критика).
- Fingerprint новых манифестов меняется из-за поля `claim_level`; прошлые манифесты не пересчитываются.
