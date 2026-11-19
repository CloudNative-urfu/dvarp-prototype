## ADDED Requirements

### Requirement: Snapshot-bound HTTP traffic
The service SHALL implement TrafficPort over HTTP on 127.0.0.1 using svckit, consume an
existing snapshot and manifest, and open episode databases read-only. It MUST NOT generate
a replacement DB, apply fixtures, calculate final metrics, or send ground truth to agents.

#### Scenario: Invalid baseline or episode
- WHEN snapshot identity/hash, manifest references, or a restored episode differ
- THEN startup or binding fails before any legal dialogue or database mutation.

#### Scenario: Shared initial state
- WHEN main-seed configurations run through SvcRunner
- THEN they restore the same physical CSV-backed snapshot and verify its logical hash.

### Requirement: Deterministic parity
The service SHALL retain the existing arrival formulas, seed derivation, corpus binding
and customer semantics. A valid invite's n_legal, blocks and mean_gap SHALL set its budget.

#### Scenario: Both services in the loop
- WHEN the same seed/plan/config run with StubBackend through traffic-svc and defender-svc
- THEN outcomes, trajectories, final DB hashes and guard decisions equal the in-process path.

### Requirement: Lifecycle and text barrier
The service SHALL expose schedule, bind, customer next/on_close/summary and flow close.
Only channel Observation fields SHALL enter a customer session; only text SHALL reach the
defender. A completed flow SHALL reject further episode calls. An exact retry SHALL not
advance a customer twice. Failures SHALL terminate the process without a silent fallback.

#### Scenario: Multiple blocks and failure cleanup
- WHEN a block completes or the service fails
- THEN its process is stopped; a later block uses a fresh process and the same invite.
