"""Процесс атакующего (ревизия 9): запускается брокером внутри bubblewrap.

Копируется в staged-каталог как `/attacker/darp_attacker_child.py`; рядом — staged-пакет
`darp_range` (только `attackers`, `isolation/{ipc,probes}` и шимы).

Вход: путь к attack-only файлу (`/input/input.json`: задание, режим, стратегия, seed,
лимиты, research-флаг; читается по сообщению `begin` — процесс может быть запущен заранее)
и строки IPC на stdin. Выход: строки IPC на stdout. Всё, что знает
процесс, — этот файл и то, что брокер прислал по IPC (Observation канала, итог сессии,
ответ своей модели). Диагностика — в stderr (брокер сохраняет её в файл прогона).
"""

from __future__ import annotations

import json
import os
import sys

from darp_range.isolation import ipc

_OUT = sys.stdout.buffer
_IN = sys.stdin.buffer


def send(msg: dict) -> None:
    _OUT.write(ipc.encode(msg, "to_parent"))
    _OUT.flush()


def recv() -> dict:
    return ipc.decode(_IN.readline(ipc.MAX_LINE_BYTES + 1), "to_child")


def request(msg: dict) -> dict:
    send(msg)
    reply = recv()
    if reply["t"] not in ("llm_response", "llm_error"):
        raise ipc.IPCProtocolError(f"ожидался ответ модели, получено {reply['t']!r}")
    return reply


def self_check() -> dict:
    """Дешёвая самопроверка процесса в каждом запуске (полный набор проб — `run_probes`)."""
    import importlib.util
    defender = [m for m in ("darp_range.executor", "darp_range.agent", "darp_range.evaluator",
                            "darp_range.guard_policy", "darp_range.guard_plugins",
                            "darp_range.state_store", "darp_range.corpus", "darp_range.runner")
                if importlib.util.find_spec(m) is not None]
    return {"env_keys": sorted(os.environ), "llm_env": sorted(k for k in os.environ
                                                              if k.startswith("RESEARCH_LLM")),
            "defender_modules_importable": defender, "cwd": os.getcwd(), "uid": os.getuid(),
            "pid": os.getpid()}


def _build(cfg: dict):
    from darp_range.attackers.attacker import Attacker
    from darp_range.attackers.composers import CorpusComposer, LLMComposer, SearchComposer
    from darp_range.attackers.types import AttackerBrief, AttackLimits
    from darp_range.event_log import ComponentWriter
    from darp_range.llm import ChatModel, Limits

    b = cfg["brief"]
    brief = AttackerBrief(brief_id=b["brief_id"], objective=b["objective"], goal=b["goal"],
                          drafts=tuple(b["drafts"]), mechanism=b["mechanism"],
                          delivery=b["delivery"])
    limits = AttackLimits(**cfg["limits"])
    writer = ComponentWriter("attacker", "attacker", send)
    strategy = cfg["strategy"]
    if strategy == "corpus":
        composer = CorpusComposer()
    elif strategy in ("template", "random"):
        composer = SearchComposer(strategy, seed=cfg["seed"])
    elif strategy == "llm":
        composer = LLMComposer(ChatModel(request, limits=Limits(**cfg["model_limits"]),
                                         writer=writer))
    else:
        raise ValueError(f"стратегия {strategy!r}")
    return Attacker(brief, mode=cfg["mode"], strategy=strategy, writer=writer, composer=composer,
                    limits=limits, research=cfg["research"])


def _obs(d: dict):
    from darp_range.channel import Observation
    return Observation(reply=d["reply"], turn=d["turn"], turns_left=d["turns_left"],
                       closed=d["closed"], research=d["research"])


def _session(d: dict):
    from darp_range.channel import SessionResult
    return SessionResult(session_id=d["session_id"], episode_id=d["episode_id"], mode=d["mode"],
                         status=d["status"], user_turns=d["user_turns"],
                         defender_turns=d["defender_turns"], violation=d["violation"],
                         transcript=tuple(tuple(r) for r in d["transcript"]))


def _error(exc: BaseException) -> dict:
    return {"t": "error", "error": type(exc).__name__, "message": str(exc),
            "code": getattr(exc, "code", None)}


def _prewarm() -> None:
    """Импорт пакета атакующего до задания: процесс можно запустить заранее (брокер держит
    один запасной процесс), задание читается только по сообщению `begin`."""
    import darp_range.attackers  # noqa: F401


def main(argv: list[str]) -> int:
    _prewarm()
    send({"t": "hello", "version": ipc.PROTOCOL_VERSION, "self_check": self_check()})
    cfg = None
    attacker = None
    att = None
    while True:
        msg = recv()
        t = msg["t"]
        if t == "shutdown":
            return 0
        if t == "run_probes":
            from darp_range.isolation import probes
            send({"t": "probe_results", "results": probes.run_all(msg["spec"], where="child")})
            continue
        if t == "begin":
            if cfg is not None:
                raise ipc.IPCProtocolError("повторный begin")
            with open(argv[1], encoding="utf-8") as f:
                cfg = json.load(f)
            send({"t": "ready"})
            continue
        if cfg is None:
            raise ipc.IPCProtocolError(f"{t!r} до begin")
        try:
            if attacker is None:
                attacker = _build(cfg)
            if t == "start_attempt":
                att = attacker.start_attempt(msg["episode_id"])
                send({"t": "started", "attempt": att.attempt})
            elif t == "observation":
                if att is None:
                    raise ipc.IPCProtocolError("observation без открытой попытки")
                if msg["close"]:
                    att.on_close(_obs(msg["obs"]))
                    send({"t": "closed"})
                else:
                    send({"t": "message", "text": att.next_message(_obs(msg["obs"]))})
            elif t == "finish_attempt":
                rec = attacker.finish_attempt(att, _session(msg["session"]))
                att = None
                send({"t": "record", "record": rec.to_json()})
            else:
                raise ipc.IPCProtocolError(f"неожиданное сообщение {t!r}")
        except ipc.IPCProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001 — ошибка передаётся брокеру с именем класса
            send(_error(exc))


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except ipc.IPCProtocolError as exc:
        print(f"IPC protocol error: {exc}", file=sys.stderr)
        sys.exit(3)
