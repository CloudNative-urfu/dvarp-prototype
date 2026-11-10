"""Сервис магазина контура возвратов (ревизия 11, `returns-operator-payout-slice`).

Процессы (stdlib, HTTP только на 127.0.0.1, порт — в `.runtime/shop_slice/<svc>/ready.json`):
- `shop-svc` — операторские approve/reject эскалированных обращений v2r с перепроверкой
  R1/R3/R4/владельца/статуса в одной транзакции и клиентский статус обращения;
- `payout-worker` — outbox `payouts` → платёжный эмулятор `pay-emu` со стабильным ключом
  идемпотентности, состоянием `unknown` и монотонной сверкой.
БД контура — отдельный файл (схема v3, только для контура); БД эпизодов runner не мигрируются.
Аутентификация — тестовая (HMAC от ключа контура), не IAM.

    python3 -m darp_range.shop_svc serve --db D --runtime-dir R --key-file K [--port N]
    python3 -m darp_range.shop_svc worker --db D --runtime-dir R --pay-ready P
Жизненный цикл целиком: `python3 scripts/shop_slice.py up|reset|smoke|down`.
"""

from .migrate import SLICE_SCHEMA_VERSION, apply_schema_v3, build_slice_db, has_schema_v3

__all__ = ["SLICE_SCHEMA_VERSION", "apply_schema_v3", "build_slice_db", "has_schema_v3"]
