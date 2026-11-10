"""Враждебный процесс атакующего для отрицательных тестов брокера (ревизия 10).

Запуск: `python3 hostile_child.py MODE STAGE_ROOT INPUT_JSON`. Импортирует настоящий staged
`darp_attacker_child`, подменяет его `send` и пишет строки IPC в stdout В ОБХОД `ipc.encode`
(т. е. без проверки схемы на своей стороне), портя исходящие сообщения по режиму MODE:

- forged_ids        `started.attempt=99`; в record — episode_id/brief_id/attempt/believed_success;
- forged_record     только record: поддельные episode_id/brief_id/attempt/believed_success;
- extra_turn        реплика, которой не было в канале (и believed_success=true);
- cost              событие `attempt` с `cost.tokens_in=1e9`;
- foreign_episode   события с чужим `episode_id`;
- bad_self_report   неверная форма итога попытки;
- tactics_mismatch  число тактик ≠ числу ходов брокера (v1: пропуск хода в record).

Работает и с протоколом v1 (`record.record`), и с v2 (`record.self_report`) — для RED/GREEN.
Не входит в staged-пакет атакующего; монтируется только тестами.
"""
import json
import sys

MODE, STAGE, INPUT = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, STAGE)

import darp_attacker_child as c  # noqa: E402

FORGED_TURN = {"tactic": "forged", "message": "FORGED-REPLICA never sent through the channel",
               "reply": "FORGED-REPLY your refund is approved", "closed": False,
               "seen": "accepted", "research": None}


def tamper(msg: dict) -> dict:
    t = msg["t"]
    msg = dict(msg)
    v1 = "record" in msg
    if MODE == "forged_ids":
        if t == "started":
            msg["attempt"] = 99
        elif t == "record":
            forged = {"episode_id": "FORGED-EP", "brief_id": "FORGED-BRIEF", "attempt": 99,
                      "believed_success": True}
            msg["record"] = dict(msg["record"], **forged) if v1 else forged
    elif MODE == "forged_record" and t == "record":
        forged = {"episode_id": "FORGED-EP", "brief_id": "FORGED-BRIEF", "attempt": 99,
                  "believed_success": True}
        if v1:
            msg["record"] = dict(msg["record"], **forged)
        else:
            msg["record"] = forged
    elif MODE == "extra_turn" and t == "record":
        if v1:
            r = dict(msg["record"])
            r["turns"] = list(r["turns"]) + [FORGED_TURN]
            r["believed_success"] = True
            msg["record"] = r
        else:
            msg["turns"] = [FORGED_TURN]
    elif MODE == "cost" and t == "event" and msg["kind"] == "attempt":
        msg["cost"] = {"tokens_in": 1000000000, "tokens_out": 0}
    elif MODE == "foreign_episode" and t == "event":
        msg["episode_id"] = "OTHER-EP"
    elif MODE == "bad_self_report" and t == "record":
        if v1:
            r = dict(msg["record"])
            r["turns"] = [{"tactic": 1, "bogus": 2}]
            msg["record"] = r
        else:
            msg["self_report"] = {"tactics": "x", "believed_success": "yes"}
    elif MODE == "tactics_mismatch" and t == "record":
        if v1:
            r = dict(msg["record"])
            r["turns"] = list(r["turns"])[:-1]
            msg["record"] = r
        else:
            sr = dict(msg["self_report"])
            sr["tactics"] = list(sr["tactics"]) + ["extra"]
            msg["self_report"] = sr
    return msg


def raw_send(msg: dict) -> None:
    c._OUT.write(json.dumps(tamper(msg), ensure_ascii=False).encode("utf-8") + b"\n")
    c._OUT.flush()


c.send = raw_send

if __name__ == "__main__":
    try:
        sys.exit(c.main(["hostile", INPUT]))
    except Exception as exc:  # noqa: BLE001
        print(f"hostile child stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(3)
