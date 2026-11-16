"""HTTP-маршруты defender-svc (issue #19). Все вызовы — POST с JSON; ответ несёт `events` —
сырые события, записанные защищаемой стороной за вызов (их пишет в журнал оркестратор).

    GET  /health                      жив ли сервис; конфигурация; загруженные пакеты darp_range
    POST /flow                        поток: run_id, research_mode, seed, режим модели, граница
    POST /episode/open                эпизод: исполнитель на БД эпизода (EpisodeInvite)
    POST /episode/defender            защита из конфигурации + FreezeGuard.check
    POST /episode/record              запись реплики канала в историю обращения (ToolPort)
    POST /episode/respond             ход защищаемой стороны → reply / closed / signals
    POST /episode/release             закрыть защиту эпизода (гварды)
    POST /episode/close               закрыть исполнитель (БД эпизода больше не пишется)

Каждое тело несёт `req` (номер вызова оркестратора) и `events` (дельту журнала оркестратора).
Повтор того же `req` (явный retry после сбоя транспорта) возвращает сохранённый ответ, не
исполняя вызов второй раз. Исключение защищаемой стороны — не сбой сервиса: ответ 200 с
`raised` (тип и текст; `fatal` — защита не совпала с замороженной), чтобы эпизод завершился
так же, как in-process. Не-200 — сбой сервиса (оркестратор: эпизод `svc-error`).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

from darp_range.agent import make_agent_model
from darp_range.agent.profiles import profile_by_name
from darp_range.channel import DefenderTurn
from darp_range.configs import ConfigSpec, FreezeGuard, FrozenConfigError
from darp_range.svckit import HTTPError, JSONHandler

from .core import DefenderEpisode, DefenderFlow
from .mirror import MirrorLog

MAX_BODY = 16 * 1024 * 1024


class State:
    def __init__(self, cfg: ConfigSpec, guard: FreezeGuard):
        self.cfg, self.guard = cfg, guard
        self.lock = threading.Lock()
        self.flow: DefenderFlow | None = None
        self.episodes: dict[str, DefenderEpisode] = {}
        self.last: tuple[Any, dict] | None = None  # (req, ответ) — для явного retry


def _turn(out: Any) -> Any:
    if type(out) is str:
        return out
    if isinstance(out, DefenderTurn):
        return {"reply": out.reply, "closed": out.closed, "signals": dict(out.signals)}
    raise TypeError(f"защищаемая сторона вернула {type(out).__name__}")


class Handler(JSONHandler):
    max_body = MAX_BODY

    def route(self, method, parts, query, body):
        st: State = self.server.state
        path = "/".join(parts)
        if method == "GET" and path == "health":
            mods = sorted(
                {
                    m.split(".")[1]
                    for m in sys.modules
                    if m.startswith("darp_range.") and m.count(".") >= 1
                }
            )
            return (
                200,
                {"ok": True, "pid": os.getpid(), "config": st.cfg.name, "darp_modules": mods},
                {},
            )
        if method != "POST" or not isinstance(body, dict):
            raise HTTPError(404, "not_found")
        with st.lock:
            req = body.get("req")
            if st.last is not None and req is not None and st.last[0] == req:
                return 200, st.last[1], {"req": req}
            t0 = time.perf_counter()
            obj = self._call(st, path, body)
            obj["svc_ms"] = (time.perf_counter() - t0) * 1000.0
            st.last = (req, obj)
            return 200, obj, {"req": req}

    def _call(self, st: State, path: str, body: dict) -> dict:
        if path == "flow":
            if body.get("config") != st.cfg.name:
                raise HTTPError(409, "config_mismatch", f"staged {st.cfg.name}")
            log = MirrorLog(body["run_id"], research_mode=bool(body.get("research_mode")))
            log.ingest(body.get("events") or ())
            mode, prof = body["agent_mode"], body.get("agent_profile", "default")
            model = make_agent_model(
                log,
                mode=mode,
                seed=int(body["seed"]),
                profile=profile_by_name(prof) if mode == "stub" else None,
            )
            st.flow = DefenderFlow(
                st.cfg, log, model, boundary=body["boundary"], freeze_guard=st.guard
            )
            st.episodes = {}
            return {"events": log.drain(), "model_spec": _jsonable(model.model_spec())}
        flow = st.flow
        if flow is None:
            raise HTTPError(409, "no_flow")
        log: MirrorLog = flow.log  # type: ignore[assignment]
        try:
            log.ingest(body.get("events") or ())
        except Exception as exc:  # noqa: BLE001 — рассинхрон журнала: сбой сервиса, не эпизода
            raise HTTPError(409, "log_desync", str(exc)) from None
        eid = body.get("episode_id")
        out: dict[str, Any] = {}
        try:
            if path == "episode/open":
                st.episodes[eid] = flow.open(**body["episode"])
            else:
                ep = st.episodes.get(eid)
                if ep is None:
                    raise HTTPError(404, "no_episode", str(eid))
                if path == "episode/defender":
                    ep.defender()  # конечная точка остаётся в сервисе; ходы — /episode/respond
                elif path == "episode/record":
                    ep.record(body["author"], body["text"])
                elif path == "episode/respond":
                    out["turn"] = _turn(ep.bundle.defender.respond(body["text"]))
                elif path == "episode/release":
                    ep.release()
                elif path == "episode/close":
                    ep.close()
                    st.episodes.pop(eid, None)
                else:
                    raise HTTPError(404, "not_found")
        except HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001 — исключение защиты уходит в эпизод как in-process
            out["raised"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "fatal": isinstance(exc, FrozenConfigError),
            }
        out["events"] = log.drain()
        return out


def _jsonable(spec: Any) -> Any:
    to_json = getattr(spec, "to_json", None)
    if callable(to_json):
        return to_json()
    return spec if isinstance(spec, (dict, list, str, int, float, type(None))) else str(spec)
