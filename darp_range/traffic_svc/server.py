"""Traffic state: verified snapshot, read-only binding and text-only customer sessions."""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from darp_range.channel import Observation
from darp_range.runner.legal_traffic import CorpusTraffic
from darp_range.runner.plan import RunPlan
from darp_range.runner.ports import FlowInvite
from darp_range.state_store.manifest import verify_manifest
from darp_range.state_store.store import load_snapshot, verify_snapshot, state_hash
from darp_range.svckit import HTTPError, JSONHandler

from .customer import client_config, ensure_model_available, make_llm_client


class State:
    def __init__(self, launch):
        self.snapshot = load_snapshot(launch["snapshot_path"])
        verify_snapshot(self.snapshot)
        self.manifest = launch["manifest"]
        verify_manifest(self.manifest, self.snapshot.path)
        for cid, account in self.manifest["accounts"].items():
            for archetype, oid in account["orders"].items():
                order = self.manifest["orders"].get(oid)
                if order is None or (order["customer_id"], order["archetype"]) != (cid, archetype):
                    raise ValueError("manifest account/archetype references a different order")
        self.snapshot_id = launch["snapshot_id"]
        self.plan = RunPlan(**launch["plan"])
        # Клиент по launch-конфигу (issue #76): scripted — прежний путь; llm — модель.
        # Живая модель проверяется здесь: отказ до ready.json, не во время диалога.
        self.client_cfg = client_config(launch)
        ensure_model_available(self.client_cfg["kind"], self.client_cfg["mode"], environ=os.environ)
        self.entries = {e["id"]: e for e in launch["entries"]}
        self.lock = threading.Lock()
        self.invite = None
        self.traffic = None
        self.clients = {}
        self.closed = False
        self.last = None

    def dispatch(self, path, body):
        if path == "flow":
            if self.closed:
                raise HTTPError(409, "flow_closed")
            if self.invite is not None:
                raise HTTPError(409, "flow_already_opened")
            invite = FlowInvite(**body["invite"])
            if (
                invite.snapshot_id != self.snapshot_id
                or invite.snapshot_hash != self.snapshot.state_hash
            ):
                raise HTTPError(409, "snapshot_mismatch")
            verify_snapshot(self.snapshot)
            # The invite, not the process launch defaults, owns the flow budget.
            if set(invite.budget) != {"n_legal", "blocks", "mean_gap"}:
                raise ValueError("budget must contain n_legal, blocks, mean_gap")
            plan = replace(self.plan, **invite.budget)
            mgr = SimpleNamespace(
                baseline_hash=self.snapshot.state_hash,
                spec=SimpleNamespace(accounts=self.manifest["accounts"]),
            )
            traffic = CorpusTraffic(SimpleNamespace(plan=plan, mgr=mgr, entries=self.entries))
            blocks = traffic.schedule(invite)
            self.invite, self.traffic = invite, traffic
            return {"blocks": [b.to_json() for b in blocks]}
        if self.closed:
            if path == "close":
                return {"end_reason": "flow_completed"}
            raise HTTPError(409, "flow_closed")
        if self.traffic is None:
            raise HTTPError(409, "no_flow")
        if path == "bind":
            if body["traffic"] != "legal":
                raise ValueError("traffic-svc accepts legal episodes only")
            entry = body["entry"]
            if entry.get("kind") != "control" or self.entries.get(entry.get("id")) != entry:
                raise ValueError("entry must be an unchanged control from the invited corpus")
            if list(self.entries).index(entry["id"]) != body["entry_index"]:
                raise ValueError("entry_index differs from invited corpus")
            db = Path(body["db_path"])
            # Before fixtures, every episode is an exact restore of this snapshot.
            if state_hash(db) != self.snapshot.state_hash:
                raise HTTPError(409, "episode_snapshot_mismatch")
            bound = self.traffic.bind(
                entry, body["entry_index"], db, episode_id=body["episode_id"], traffic="legal"
            )
            handle = str(len(self.clients))
            if self.client_cfg["kind"] == "scripted":
                client = self.traffic.client(bound)
            else:
                client = make_llm_client(
                    bound,
                    kind=self.client_cfg["kind"],
                    mode=self.client_cfg["mode"],
                    invite_seed=self.invite.seed,
                    episode_id=body["episode_id"],
                    environ=os.environ,
                )
            self.clients[handle] = client
            return {
                "actor_id": bound.actor_id,
                "ticket_id": bound.ticket_id,
                "order_id": bound.order_id,
                "fixture": bound.fixture,
                "truth": bound.truth,
                "bound": bound.handle.to_json(),
                "handle": handle,
            }
        if path == "close":
            self.clients.clear()
            self.traffic.close()
            self.closed = True
            return {"end_reason": "flow_completed"}
        client = self.clients.get(body.get("handle"))
        if client is None:
            raise HTTPError(404, "no_client")
        if path in ("client/next", "client/close"):
            # Explicit channel allowlist: no DB, ground truth or telemetry in observations.
            obs = Observation(**body["observation"])
            if path == "client/next":
                return {"text": client.next_message(obs)}
            client.on_close(obs)
            return {}
        if path == "client/summary":
            return {"summary": client.summary(), "end_reason": client.end_reason}
        raise HTTPError(404, "not_found")


class Handler(JSONHandler):
    max_body = 16 * 1024 * 1024

    def route(self, method, parts, query, body):
        st = self.server.state
        path = "/".join(parts)
        if method == "GET" and path == "health":
            return 200, {"ok": True, "snapshot_hash": st.snapshot.state_hash}, {}
        if method != "POST" or not isinstance(body, dict):
            raise HTTPError(404, "not_found")
        with st.lock:
            # Only an exact retry may reuse a response; never replay a changed body.
            if st.last is not None and body.get("req") == st.last[0].get("req"):
                if (path, body) != (st.last[1], st.last[0]):
                    raise HTTPError(409, "request_conflict")
                return 200, st.last[2], {}
            try:
                result = st.dispatch(path, body)
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPError(400, "invalid_request", str(exc)) from None
            st.last = (body, path, result)
            return 200, result, {"req": body.get("req")}
