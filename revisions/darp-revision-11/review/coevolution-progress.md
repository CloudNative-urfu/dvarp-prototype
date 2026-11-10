# Коэволюция: итог независимой повторной проверки revision11

OpenCode / openai/gpt-6.1-sol; прежний Claude заменён. Фаза foundation, не production.
D11-S1 закрыт; свидетельства: `artifacts/openai-logs/recheck/tests.log`,
`artifacts/openai-logs/recheck/independent-negatives.log`,
`artifacts/openai-logs/recheck/smoke/smoke.json`.

## I. Компоненты магазина

| Пункт | Статус программы | Компоненты / API / права | Проверенные сбои и путь | Остаток |
|---|---|---|---|---|
| I.1 | partial | SQLite v3 магазина + независимая книга; HTTP /v1 оператор/клиент, прежние пять инструментов через Executor | MigrationScope и новый smoke PASS | Нет HTTP-адаптера инструментов |
| I.2 | partial | Реальный shop-svc: синтетически аутентифицированный оператор approve/reject, append-only решения; клиент только читает своё | auth/ownership/сумма/final-sale, конкуренция/replay/restart PASS | Операторская задача implemented; вложения отсутствуют, defect_verified — утверждение; ручная очередь отдельно |
| I.3 | implemented | Отдельный pay-emu и SQLite ledger, синтетический payee из charge, ключ идемпотентности, fault только оценщика | drop до/после accept, pending/restart/failed и запрет подмены в новом smoke PASS | Подмножество провайдера; нет TTL ключа, lookup по ключу — допущение |
| I.4 | partial | outbox + отдельный payout-worker + GET reconciliation, монотонные состояния | D11-S1 исправлен: последний POST + SIGKILL + restart → succeeded, pending/restart → succeeded, без второй выплаты/POST; дополнительные негативы PASS | Polling-срез implemented, подписанных push-событий нет |
| I.5 | pending | Не реализован | Не проверялся | Сначала конкретное правило владельца, склад не добавлять произвольно |
| I.6 | partial | Реальные процессы shop-svc/pay-emu/worker; scripted agent через прежние пять инструментов | high_value → серверная эскалация → HTTP approve → выплата → клиент refunded; smoke17/17, oracle/отрицательный контроль PASS | Offline-задача implemented; живого среза нет |

Теперь согласование и выплата действительно создаются при исполнении среза,
не заранее из GT. Но runner-эпизоды пока используют fixture согласований, а очередь
времени остаётся моделью, не измерением операторской работы в этом контуре.

## A. Изоляция/IPC

Согласованная задача broker-fail-closed **implemented**. Координатор выполнил отдельную
VM-проверку исходного snapshot: 6/6 брокерских тестов без SKIP и M8 killed, control PASS
(`candidate/artifacts/openai-fix/broker-vm.log`). Это не мой запуск; брокер не менялся
при payout-ремонте по сведениям координатора. Старый host SKIP сохранён исторически.

Программный уровень A остаётся **partial**: защита не OS-изолирована, blind происхождение
не доказано, strict-black-box не заявляется. Расширение этих требований не нужно
для закрытия минимального D11-S1.

## B / C

**pending**: долговечный раздельный опыт/provenance и лига замороженных соперников
этой ревизией не реализованы и не проверены. Исторический replay не заменяет B/C.

Все пять OpenSpec-задач текущего ограниченного среза implemented, но это не означает
завершения всей программы. Нового блокирующего дефекта в scoped re-review нет.
Итог готов к публикации координатором с ограничениями из `artifacts/critique.md`.
Revision12 не запускалась; этот документ не даёт разрешения её запускать.
