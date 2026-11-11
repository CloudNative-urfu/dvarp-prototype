"""Платёжный эмулятор контура возвратов (ревизия 11, `payment-emulator-service`).

Отдельный процесс `pay-emu` (HTTP на 127.0.0.1) со своей SQLite-книгой: синтетические платежи
по доставленным заказам, возвраты с обязательным `Idempotency-Key`, состояния
pending → succeeded | failed, получатель фиксирован платежом, лимит суммы возвратов по платежу,
сбои только из файла оценщика. Не интеграция с реальным провайдером (NFR-2.13).

    python3 -m darp_range.pay_emu create --ledger L --shop-db S
    python3 -m darp_range.pay_emu serve --ledger L --runtime-dir D [--fault-file F] [--port N]
"""

from .ledger import LEDGER_SCHEMA_VERSION, LedgerError, TransitionError

__all__ = ["LEDGER_SCHEMA_VERSION", "LedgerError", "TransitionError"]
