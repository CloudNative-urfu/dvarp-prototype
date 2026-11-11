## Why

The coevolution program (level A) requires that black-box claims rest on an enforced boundary. Today the attacker and defender share one Python process: attacker code reaches the shared `EventLog` through `ComponentWriter._emit.__self__` and reads defender events (local probe L1, `artifacts/sources.md`); the live model key lives in the same process environment; legacy corpus briefs were written with knowledge of the defence but carry no provenance; and the exact attacker model input is not audited. No run can honestly be called strict-black-box, and levels B/C (memory, league) cannot start.

## What Changes

- Add a machine-readable threat model and access matrix (subject × resource → allow/deny, mechanism, test id); boundary-2r is the current defence, boundary-1 is legacy; fix the misleading `event_log` docstring.
- Run the attacker side (modes D and A) in a separate bubblewrap process; the parent acts as broker for the channel, LLM key and logs; JSON-lines IPC with a closed schema. The in-process path stays as a labelled historical research baseline.
- Add executable OS negative probes run from inside the attacker process, with sensitivity controls (in-process and weak-sandbox) and positive controls.
- Audit exact attacker model payloads (no secrets), plant synthetic canaries, and test invariance of attacker input under hidden policy/GT/prompt changes; record system-prompt leakage through agent replies as a separate outcome without post-filtering.
- Add provenance to attacker briefs and a computed run `claim_level`; existing corpora are `implementation-informed` and cannot support a black-box claim.
- No change to server rules R1/R2/R5, executor, legal client, or live-model budget.

## Capabilities

### New Capabilities
- `attacker-isolation`: access matrix, sandboxed attacker process with broker IPC, OS negative probes, payload audit and input invariance.
- `attack-provenance`: brief provenance registry and computed claim level of a run.

### Modified Capabilities
- (none; no existing specs in the prototype)

## Impact

- New modules (e.g. `darp_range/isolation/`), `configs/access_matrix.json`, `scripts/isolation_probes.py`, new tests; small adapters in `darp_range/attackers` and the run manifest.
- Requires bubblewrap and unprivileged user namespaces; without them runs are labelled `in_process_research` rather than silently unsandboxed.
- Offline only, 0 Qwen requests. New checks form a separate fast command (≤5 min), not added to the 50-minute verify.
