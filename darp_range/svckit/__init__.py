"""Общий набор процессов контура возвратов (ревизия 11, `returns-operator-payout-slice`).

Только стандартная библиотека: HTTP/JSON-сервер на 127.0.0.1 с файлом готовности, клиент
с явным различением «ответ получен» / «соединение оборвано» и журнал запросов JSONL с
корреляционными id. Используется процессами `pay_emu` и `shop_svc`; независимый оракул
среза (`scripts/shop_slice_oracle.py`) этот пакет не импортирует.
"""

from .http import (
    Drop, JSONHandler, HTTPError, LoopbackUnavailable, ServiceServer, TransportError, call,
    read_ready, write_json_atomic,
)

__all__ = ["Drop", "JSONHandler", "HTTPError", "LoopbackUnavailable", "ServiceServer", "TransportError",
           "call", "read_ready", "write_json_atomic"]
