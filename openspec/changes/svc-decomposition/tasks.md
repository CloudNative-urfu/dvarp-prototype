# Tasks: svc-decomposition

## 1. Phase 0 — seams

- [ ] seams `DefenderPort` (defender turn interface) and `ToolPort` (tool execution interface)
      extracted from `runner/flow.py`; in-process adapters preserve current behavior
- [ ] existing unit tests green (`ruff check .`, module-targeted unittest runs); no changes to
      frozen defense code (`darp_range/{configs,guard_contract,guard_plugins,guard_policy,
      simple_baseline,executor,tools}/`)

## 2. Phase 1 — defender-svc + parity

- [ ] package `darp_range/defender_svc/`: server (svckit HTTP on 127.0.0.1), session
      (frozen config staging + hash checks + per-episode `FreezeGuard.check`), `/start`,
      `/turn`, `/finish`, `/health`
- [ ] tool callback client in the service + `/tool-callback` data API on the orchestrator
      side (executor op via toolbox semantics, `PendingCall` contract preserved)
- [ ] orchestrator-side lifecycle: staging, spawn (port 0 → argv), ready.json, pids.json
      registry, shutdown; single-writer rule (svc returns records, orchestrator writes)
- [ ] meters: scoped meter budget at `/start`, per-turn cost records, aggregation into
      `RunMeter`/`ChainedMeter`/`ScopedMeter` unchanged
- [ ] parity test: service-mode offline run (StubBackend, same seed/corpus/config) reproduces
      the in-process run — identical episode outcomes (final state hashes), guard decision
      sequences, cost aggregates within the transport bucket
- [ ] fail-closed behavior: svc crash / timeout / non-200 → episode status `svc-error`
      recorded honestly; single explicit retry visible in the event log

## 3. Phase 2 — live mode + accounting

- [ ] live mode through the service (`RESEARCH_LLM_*` passed via env); migration of
      `scripts/live_mode_a_v.py` to the service path
- [ ] honest accounting: transport-overhead bucket (bytes, wall time of HTTP hops) as a
      separate defender-borne line in the report; L* parity tolerance documented
- [ ] `scripts/verify.py`: in-process path unchanged + service-mode smoke step
- [ ] docs: legacy marking of `Runner`/`report_cli offline` updated after parity passes;
      `docs/traceability.md` rows touched by this change updated

## 4. Acceptance

- [ ] parity gate passes (see Phase 1 test)
- [ ] `runtime_requirements_empty` and `import_audit` still pass (stdlib-only)
- [ ] svc refuses to start on config drift; per-episode freeze check inside the service
- [ ] acceptance checks of `report_cli` untouched on the in-process path
- [ ] ruff + unit tests green; PR to `develop` with `Closes #19`
