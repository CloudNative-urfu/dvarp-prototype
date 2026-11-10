"""Закрытая схема IPC брокер ↔ процесс атакующего (JSON lines), ревизия 9; версия 2 — ревизия 10.

Модуль самодостаточен (только стандартная библиотека): он копируется в staged-пакет
процесса атакующего (`darp_range/isolation/ipc.py`) и импортируется брокером как
`darp_range.isolation.ipc`. Обе стороны проверяют каждое сообщение одной функцией
`check_message`: неизвестный тип, неизвестное или недостающее поле, неверный тип значения —
`IPCProtocolError`. Брокер превращает её в нарушение протокола попытки, а не в успех защиты.

Через IPC не передаются ни ключ, ни URL модели, ни заголовки: в `llm_request` — только
сообщения `{role, content}` и id эпизода, в `llm_response` — текст, finish_reason и счёт
токенов (`usage`).

Версия 2 (ревизия 10, broker-authoritative-record): итог попытки собирает брокер из своей
копии канала. Ребёнок в `record` присылает только `self_report {tactics: [str],
believed_success: bool}` (самоотчёт); `message` несёт `note` (причина отказа от реплики,
только при `text=null`). События ребёнка: `t_sim` всегда null; `attempt` и
`protocol_violation` — `cost=null`; `message_composed` — cost только `latency_ms`/`cpu_ms`.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

PROTOCOL_VERSION = 2
MAX_LINE_BYTES = 4 * 1024 * 1024   # одна строка IPC; больше — нарушение протокола


class IPCProtocolError(RuntimeError):
    """Сообщение вне закрытой схемы IPC."""


_STR = (str,)
_OSTR = (str, type(None))
_INT = (int,)
_BOOL = (bool,)
_DICT = (dict,)
_ODICT = (dict, type(None))
_LIST = (list,)
_NUM = (int, float)
_ONUM = (int, float, type(None))

# тип → {поле: допустимые типы}; все поля обязательны, лишние запрещены.
PARENT_TO_CHILD: dict[str, dict[str, tuple]] = {
    "begin": {},
    "start_attempt": {"episode_id": _STR},
    "observation": {"obs": _DICT, "close": _BOOL},
    "finish_attempt": {"session": _DICT},
    "llm_response": {"content": _STR, "finish_reason": _STR, "usage": _DICT},
    "llm_error": {"error": _STR, "message": _STR, "limit": _ODICT},
    "shutdown": {},
}
CHILD_TO_PARENT: dict[str, dict[str, tuple]] = {
    "hello": {"version": _INT, "self_check": _DICT},
    "ready": {},
    "event": {"kind": _STR, "data": _ODICT, "episode_id": _OSTR, "cost": _ODICT, "t_sim": _ONUM},
    "llm_request": {"messages": _LIST, "episode_id": _OSTR},
    "started": {"attempt": _INT},
    "message": {"text": _OSTR, "note": _OSTR},
    "closed": {},
    "record": {"self_report": _DICT},
    "error": {"error": _STR, "message": _STR, "code": _OSTR},
}
OBS_FIELDS = {"reply": _OSTR, "turn": _INT, "turns_left": _INT, "closed": _BOOL,
              "research": _ODICT}
SESSION_FIELDS = {"session_id": _STR, "episode_id": _STR, "mode": _STR, "status": _STR,
                  "user_turns": _INT, "defender_turns": _INT, "violation": _OSTR,
                  "transcript": _LIST}
USAGE_FIELDS = {"tokens_in": _INT, "tokens_out": _INT, "source": _STR}
MESSAGE_FIELDS = {"role": _STR, "content": _STR}
SELF_REPORT_FIELDS = {"tactics": _LIST, "believed_success": _BOOL}
# затраты, которые процесс атакующего может сообщить о себе (время композиции; токены и
# запросы модели пишет только ChatModel брокера)
CHILD_COST_FIELDS = frozenset({"latency_ms", "cpu_ms"})
LIMIT_FIELDS = {"scope": _STR, "limit_name": _STR, "limit": _INT, "used": _INT, "owner": _STR}


def _check_fields(obj: Any, schema: Mapping[str, tuple], where: str) -> None:
    if not isinstance(obj, dict):
        raise IPCProtocolError(f"{where}: ожидался объект")
    extra = sorted(set(obj) - set(schema))
    missing = sorted(set(schema) - set(obj))
    if extra:
        raise IPCProtocolError(f"{where}: поля вне схемы {extra}")
    if missing:
        raise IPCProtocolError(f"{where}: нет полей {missing}")
    for k, types in schema.items():
        v = obj[k]
        if isinstance(v, bool) and bool not in types:
            raise IPCProtocolError(f"{where}.{k}: bool вместо {[t.__name__ for t in types]}")
        if not isinstance(v, types):
            raise IPCProtocolError(f"{where}.{k}: тип {type(v).__name__} вне схемы")


def check_message(msg: Any, direction: str) -> dict:
    """Проверить сообщение направления `to_child`/`to_parent`; вернуть его же."""
    table = PARENT_TO_CHILD if direction == "to_child" else CHILD_TO_PARENT
    if direction not in ("to_child", "to_parent"):
        raise ValueError(direction)
    if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
        raise IPCProtocolError("сообщение без строкового поля t")
    t = msg["t"]
    if t not in table:
        raise IPCProtocolError(f"тип {t!r} вне схемы {direction}")
    body = {k: v for k, v in msg.items() if k != "t"}
    _check_fields(body, table[t], t)
    if t == "observation":
        _check_fields(body["obs"], OBS_FIELDS, "observation.obs")
    elif t == "finish_attempt":
        _check_fields(body["session"], SESSION_FIELDS, "finish_attempt.session")
        for row in body["session"]["transcript"]:
            if not (isinstance(row, list) and len(row) == 2 and all(isinstance(x, str) for x in row)):
                raise IPCProtocolError("finish_attempt.session.transcript: строки [direction, text]")
    elif t == "llm_response":
        _check_fields(body["usage"], USAGE_FIELDS, "llm_response.usage")
    elif t == "llm_error" and body["limit"] is not None:
        _check_fields(body["limit"], LIMIT_FIELDS, "llm_error.limit")
    elif t == "llm_request":
        if not body["messages"]:
            raise IPCProtocolError("llm_request.messages пуст")
        for i, m in enumerate(body["messages"]):
            _check_fields(m, MESSAGE_FIELDS, f"llm_request.messages[{i}]")
    elif t == "event":
        if body["kind"] not in ("attempt", "message_composed", "protocol_violation"):
            # llm_call пишет только ChatModel брокера; ребёнок не может подделать затраты модели
            raise IPCProtocolError(f"event.kind {body['kind']!r} не разрешён процессу атакующего")
        if body["t_sim"] is not None:
            raise IPCProtocolError("event.t_sim: время симуляции задаёт брокер, не атакующий")
        cost = body["cost"]
        if body["kind"] != "message_composed":
            if cost is not None:
                raise IPCProtocolError(f"event {body['kind']}: cost должен быть null")
        elif cost is not None:
            extra = sorted(set(cost) - CHILD_COST_FIELDS)
            if extra:
                raise IPCProtocolError(f"event.cost: поля вне схемы {extra}")
            for k, v in cost.items():
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v < 3.6e6:
                    raise IPCProtocolError(f"event.cost.{k}: недопустимое значение")
    elif t == "record":
        sr = body["self_report"]
        _check_fields(sr, SELF_REPORT_FIELDS, "record.self_report")
        if not all(isinstance(x, str) for x in sr["tactics"]):
            raise IPCProtocolError("record.self_report.tactics: только строки")
    elif t == "message" and body["text"] is not None and body["note"] is not None:
        raise IPCProtocolError("message.note допустим только при text=null")
    return msg


def encode(msg: Mapping[str, Any], direction: str) -> bytes:
    check_message(dict(msg), direction)
    line = json.dumps(msg, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8") + b"\n"
    if len(line) > MAX_LINE_BYTES:
        raise IPCProtocolError("строка IPC длиннее предела")
    return line


def decode(line: bytes, direction: str) -> dict:
    if not line:
        raise IPCProtocolError("канал IPC закрыт (EOF)")
    if len(line) > MAX_LINE_BYTES:
        raise IPCProtocolError("строка IPC длиннее предела")
    try:
        msg = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IPCProtocolError(f"строка IPC не JSON: {exc}") from exc
    return check_message(msg, direction)
