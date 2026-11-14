# Design: svc-decomposition

## Context

Runtime facts (zero-version, revision 11 scaffold):

- Orchestration: `Runner` (`darp_range/runner/orchestrator.py`) runs paired base/attacked
  episodes per frozen config cell; an episode restores the DB snapshot
  (`state_store/store.py`), applies the corpus fixture, builds executor on the episode DB
  (`runner/flow.py`), assembles the defender from the frozen config
  (`configs/build.py` `assemble_defender` + `FreezeGuard.check` per episode).
- Defense: guard pipeline is a `ToolInterceptor` (`guard_policy/defender.py`) wired into the
  toolbox interception points (`tools/toolbox.py`); agent loop `RefundAgent`
  (`agent/agent.py`) behind `channel.DefenderEndpoint`; model via `ChatModel`
  (`llm/client.py`, stdlib-urllib OpenAI-compatible backend, `meter.admit` before each call).
- Service building blocks already exist: `svckit/http.py` (stdlib `ThreadingHTTPServer` on
  127.0.0.1, JSON handler, atomic `ready.json`, `call()` client), subprocess lifecycle
  pattern `shop_svc/runtime.py` (`Popen(start_new_session)` + `pids.json`).
- Constraints enforced by tests: runtime deps must stay empty (`runtime_requirements_empty`),
  imports limited to stdlib (`import_audit`), defense code frozen by hashes
  (`configs.lock.json`, `FreezeGuard.check`), attacker isolation must not be simplified.
- Direction: `docs/brief.md` — services expose versioned local APIs; no direct LLM access
  to the shared SQLite; vertical slices.

## Goals / Non-Goals

- Goals: defender (config + guards + reaction policy + escalation rules + agent + LLM client)
  as an HTTP service on 127.0.0.1; script orchestrator as the single owner of data,
  snapshots, aggregation (`E_L`/`E_A`, analytic queue block `λ_Q = λ_L·E_L + λ_A·E_A`) and
  the report; two strictly separated launch contours inside the orchestrator; a parity gate
  proving service mode equals the in-process reference; honest accounting that does not
  hide transport overhead.
- Non-Goals: decomposing the attacker (bwrap/IPC stays as is); moving executor or episode
  DB into the service; rewriting metrics/evaluator; hot config reload (config stays
  load-once per service process); touching frozen defense code.

## Variant choice

- **A1 — thin defender-svc (chosen).** The service holds the frozen config, guard pipeline,
  agent and LLM client; the orchestrator holds executor + episode DB and exposes a data API
  the defender's tool calls go to over HTTP. Matches the real-system boundary
  (agent service ↔ API gateway), keeps the DB with a single owner, and preserves the
  in-process `ToolInterceptor` contract inside the service.
- **A2 — fat service (rejected).** Executor + episode DB inside the service: two processes
  against one SQLite file race with snapshot/restore, and the service touches the shared DB
  directly, contradicting `docs/brief.md`.
- **A3 — seams only (rejected as the end state).** Interfaces without a process split do
  not reach the goal; used only as Phase 0 inside A1.

## Decisions

- Decision: variant A1, phased (0 spec → 1 seams → 2 service + parity → 3 live migration).
- Decision: **two strictly separated launch contours inside the orchestrator** (defender-side
  and attack-side) around a thin pair-scheduling core: no mutual imports, no shared mutable
  state; the channel is the only mediator between the contours. Import isolation is enforced
  by an architecture test (attack-side does not import defender-side and vice versa), in the
  spirit of the existing `import_audit`.
- Decision: **attack-side contour is the polygon connection contract** (scenario change,
  2026-11-13): the orchestrator opens an attack cycle by a start signal (episode invite:
  config, seed, budget) and receives a completion signal (attack cycle finished, raw records
  of the attacking side transferred). The polygon is a separate future service; until it
  exists, the current attacker (bwrap + JSON-lines IPC, text-only) runs in a transitional
  mode behind the same contour — the swap stays local to the contour. The attack corpus is
  transitional in the СС and moves to the polygon repo later.
- Decision: **telemetry barrier** — per-turn telemetry (guard triggers, on-the-spot diff
  verdicts, costs) travels alongside the dialog reply but is never visible to the
  attacking agent; the text channel and the telemetry stream are separate channels,
  enforced by the architecture test. Final metrics are computed by `metrics-svc`
  (issue #33, separate task; out of this change's scope) — the СС does not compute final
  metrics (single source of truth). Canon: dvarp#20 (NFR-2.8 mix-run, СС↔polygon contract).
- Decision: **passive raw-record instrumentation inside the service** — collectors subscribe
  to the same raw events (guard journal, costs, decisions, escalation/clarify events) and do
  not affect agent behavior; only raw records with episode/turn ids leave the service; the
  traffic class (`E_L` natural+FP vs `E_A`) is attributed by the orchestrator. Aggregation
  and the analytic queue block stay orchestrator-side.
- Decision: **traffic-svc is a separate service with an explicit contract** (team-lead
  decision, 2026-11-14, issue #36): the legal traffic simulation internals (arrivals,
  schedule blocks, `base`/`attacked` variants) are carved out of the orchestrator's
  defender-side contour into a separate service implemented by BruhWhyMeHaveNoIdea. The
  defender-side contour owns only the contract: the episode/flow invite carries config,
  seed, budget and the **id + hash of the DB snapshot**; until traffic-svc exists, the
  current simulation internals (`manual_queue.build_flow`, `plan.build_schedule`) run
  behind the same contract interface — the swap stays local to the contour.
- Decision: **legal episodes bind to real DB state** (team-lead decision, 2026-11-14):
  legal traffic episodes reference real rows of the generated order base — orders,
  positions, tickets consistent with rules R1–R7 and the corpus ground truth
  (`pick_accounts`/`corpus.bind` semantics); placeholder identifiers are forbidden.
  The snapshot id + hash in the episode invite is the anchor; the snapshot generator
  (deterministic, hash-checked on restore) is the single base state for every config
  start of a run.
- Decision: **DB snapshot contract** — every run starts from the same initial state
  (deterministic generator, hash-checked snapshot restore), as hatched on the owner's
  vision diagram; editing the generator/schema means regenerating snapshots and corpus, not
  re-freezing defense code.
- Decision: canon v2 alignment — corpus v2 (40 entries, goals `break`/`slow`/`burn`, no
  `shift`; escalation is a neutral outcome), 24 frozen configurations with three canon
  reaction policies (`canon_isolate`/`canon_escalate`/`canon_terminate`), FR-1.20 scripted
  multi-turn; spec aligned with dvarp#17 and the queue-analytics revision dvarp#18.
- Decision: one service process per config cell; the orchestrator stages the frozen config
  directory (copy of `configs/` + `configs.lock.json`) to a per-cell temp dir and passes it
  via env (`DARP_SVC_CONFIG_DIR`). The service verifies hashes itself (`load_frozen` +
  `FreezeGuard.check` against the repo checkout) and refuses to serve on drift
  (process exits non-zero before binding the port).
- Decision: the service re-reads frozen defense code from the repo checkout (read-only);
  the service's own code lives in `darp_range/defender_svc/` and never edits frozen modules.
- Decision: single-writer rule — the orchestrator owns the EventLog writers and the episode
  DB. The service returns per-turn records (guard journal entries, model-call usage, agent
  events) in the HTTP response; the orchestrator writes them to its log/DB in the original
  order. No service-side writes to DB or JSONL.
- Decision: meters — the service receives a scoped meter budget (run/cell/episode ids and
  limits) at session start and answers each turn with the cost records; the orchestrator
  aggregates them into `RunMeter`/`ChainedMeter`/`ScopedMeter` as today. `meter.admit`
  stays inside the service (before each model call).
- Decision: fail-closed both directions — service crash, connection timeout or non-200
  response aborts the episode honestly (episode status `svc-error`, recorded in the report),
  no silent skip. One explicit retry with a prespawned standby process is allowed and must
  be visible in the event log.
- Decision: transport overhead (HTTP hop for tool calls and turns: bytes, wall time) is
  recorded in a separate honest-accounting bucket (defender-borne), never merged into
  guard or agent costs; L* parity is checked within a declared tolerance.
- Decision: parity gate — an offline run in service mode with `StubBackend` and the same
  seed/corpus/config must reproduce the in-process run: identical episode outcomes (final
  state hashes), identical guard decision sequences, cost aggregates equal within the
  transport bucket. Verified by a dedicated parity test and later by a `verify.py` smoke.
- Decision: `scripts/verify.py` keeps running the in-process path (frozen defense
  acceptance untouched); service mode adds its own smoke step. Legacy marking of
  `Runner`/`report_cli offline` stays until parity passes.
- Decision: port discovery — the orchestrator binds port 0, passes the resolved port to the
  child via argv, keeps the lifecycle in a `pids.json`-style registry (reuse
  `shop_svc/runtime.py` pattern), shuts the service down at run end.

## HTTP Contracts

### Orchestrator → defender-svc (defender side)

- `POST /start` — body: `{episode_id, seed, corpus_fixture_ref, meter_scope, limits}`.
  Response: `{ok}` or `{error}`. Runs `FreezeGuard.check` for this episode.
- `POST /turn` — body: `{turn_id, attacker_message}` (plus attachments per `channel/types`).
  The service runs the agent loop for this turn: guard pipeline at input, model calls with
  `meter.admit`, tool calls sent to the callback endpoint (below), guard pipeline at
  output/context/tool. Response: `{reply, guard_journal[], model_usage[], agent_events[],
  transport_hint}`. HTTP error or timeout → episode aborted (fail-closed).
- `POST /finish` — body: `{status}`; closes the session, releases per-episode state.
- `GET /health` — liveness for probes.

### defender-svc → orchestrator (tool callback, data API)

- `POST /tool-callback` — body: `{episode_id, turn_id, seq, tool_name, stage, args}`.
  The orchestrator executes the executor operation through the toolbox semantics
  (`PendingCall` → result/error) on the episode DB. Response: `{result}` or
  `{error, kind}`. The guard interception (`before_call` / `after_read`) already happened
  inside the service; the callback carries the decision (`allow`), or the call is not sent.
- Timeouts: service → callback 60 s; orchestrator → service connect 10 s, request 600 s
  (tunable via env). All failures fail-closed.

## Risks / Trade-offs

- HTTP back-channel adds latency and code; mitigated by the transport bucket and the parity
  tolerance, and by reusing `svckit/http.py` instead of adding dependencies.
- Ordering of events crossing two processes can reorder; mitigated by the single-writer
  rule and per-turn sequence numbers (`seq`) in responses.
- Freeze discipline must hold across the process boundary; mitigated by staging + in-service
  hash checks + per-episode `FreezeGuard.check` inside the service.
- Legacy in-process path and service path can drift; mitigated by the parity test being part
  of the acceptance and the verify smoke.

## Migration Plan

1. Phase 0: spec aligned with canon (done on the branch).
2. Phase 1: `DefenderPort`/`ToolPort` seams with in-process adapters; tests green.
3. Phase 2: implement `darp_range/defender_svc/` (server, session, staging, client), two-
   contour orchestrator + architecture test, parity test (StubBackend).
4. Phase 3: live mode through the service, migrate `scripts/live_mode_a_v.py`, transport
   bucket in the report, docs/legacy-marking update, verify smoke.
