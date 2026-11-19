"""HTTP TrafficPort adapter; traffic generation and client state stay in the service."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from darp_range.channel import Observation
from darp_range.corpus import BoundEpisode as CorpusEpisode
from darp_range.state_store.manifest import build_manifest
from darp_range.svckit import ServiceProcess, TransportError, call, write_json_atomic

from .plan import BlockSchedule, FlowSlot, RunnerError
from .ports import BoundEpisode, FlowInvite

ROOT = Path(__file__).resolve().parents[2]


class TrafficServiceError(RunnerError):
    """Traffic service failed: stop the run rather than invent episodes."""


class SvcTraffic:
    def __init__(self, ctx):
        self.ctx = ctx
        self.process = None
        self.port = None
        self.req = 0
        self.invite = None
        self.failed = False
        self.workdir = ctx.mgr.workdir / "traffic-svc" / uuid4().hex
        self.serial = 0
        self.launch = {
            "snapshot_path": str(Path(ctx.mgr.snapshot.path).resolve()),
            "snapshot_id": f"seed-{ctx.mgr.spec.seed}/schema-v{ctx.mgr.schema_version}",
            "manifest": build_manifest(ctx.mgr.snapshot.path, ctx.mgr.spec),
            "plan": asdict(ctx.plan),
            "entries": list(ctx.entries.values()),
            # Клиент легального трафика (issue #76): scripted | llm — решение плана.
            "client": {"kind": getattr(ctx.plan, "customer_mode", "scripted")},
        }

    def _start(self):
        if self.failed:
            raise TrafficServiceError("traffic-svc is unavailable after a failed call")
        if self.process is not None:
            return
        self.serial += 1
        work = (self.workdir / str(self.serial)).resolve()
        launch = work / "launch.json"
        write_json_atomic(launch, self.launch)
        process = ServiceProcess(
            "darp_range.traffic_svc", ["--launch", str(launch)], work, name="traffic-svc", root=ROOT
        )
        try:
            self.port = process.start()
            self.process = process
            if self.invite is not None:
                self._post("flow", {"invite": self.invite.to_json()})
        except Exception:
            process.stop()
            self.process = None
            self.failed = True
            raise

    def _post(self, path, body=None):
        if self.process is None:
            raise TrafficServiceError("traffic-svc flow is closed")
        self.req += 1
        payload = dict(body or {}, req=self.req)
        try:
            try:
                status, result = call(self.port, "POST", "/" + path, payload, timeout=60)
            except TransportError:
                # Exact retry: the server caches the last mutation before replying.
                status, result = call(self.port, "POST", "/" + path, payload, timeout=60)
            if status != 200:
                raise TrafficServiceError(f"traffic-svc /{path}: HTTP {status}: {result}")
            return result
        except (TransportError, TrafficServiceError) as exc:
            self.failed = True
            if self.process is not None:
                self.process.stop()
                self.process = None
            raise TrafficServiceError(str(exc)) from exc

    def schedule(self, invite: FlowInvite):
        self._start()
        result = self._post("flow", {"invite": invite.to_json()})
        self.invite = invite
        return tuple(
            BlockSchedule(b["block"], b["flow_seed"], tuple(FlowSlot(**s) for s in b["slots"]))
            for b in result["blocks"]
        )

    def bind(self, entry, entry_index, db_path, *, episode_id, traffic):
        self._start()
        if self.invite is None:
            # FlowContext also supports direct block execution (without schedule()).
            self.schedule(
                FlowInvite(
                    config=getattr(getattr(self.ctx, "cfg", None), "name", "flow"),
                    seed=self.ctx.seed,
                    budget={
                        k: getattr(self.ctx.plan, k) for k in ("n_legal", "blocks", "mean_gap")
                    },
                    snapshot_id=self.launch["snapshot_id"],
                    snapshot_hash=self.ctx.mgr.baseline_hash,
                )
            )
        result = self._post(
            "bind",
            {
                "entry": dict(entry),
                "entry_index": entry_index,
                "db_path": str(Path(db_path).resolve()),
                "episode_id": episode_id,
                "traffic": traffic,
            },
        )
        bound = CorpusEpisode(**result["bound"])
        ep = BoundEpisode(
            result["actor_id"],
            result["ticket_id"],
            result["order_id"],
            result["fixture"],
            result["truth"],
            bound,
        )
        # Harness metadata is local; the customer endpoint receives only its opaque handle.
        ep._remote_client = result["handle"]
        return ep

    def client(self, ep):
        return RemoteUser(self, ep._remote_client)

    def close(self):
        result = {"end_reason": "flow_completed"}
        if self.process is not None:
            try:
                if not self.failed and self.invite is not None:
                    result = self._post("close")
            finally:
                if self.process is not None:
                    self.process.stop()
                    self.process = None
        return result


class RemoteUser:
    def __init__(self, traffic, handle):
        self.traffic, self.handle = traffic, handle

    def _observation(self, path, obs: Observation):
        return self.traffic._post(path, {"handle": self.handle, "observation": asdict(obs)})

    def next_message(self, obs):
        return self._observation("client/next", obs)["text"]

    def on_close(self, obs):
        self._observation("client/close", obs)

    def summary(self):
        return self.traffic._post("client/summary", {"handle": self.handle})["summary"]

    @property
    def end_reason(self):
        return self.traffic._post("client/summary", {"handle": self.handle})["end_reason"]
