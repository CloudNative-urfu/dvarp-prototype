## Why

The scaffold was produced by an autonomous factory loop; the team selected the last revision
(issue #2 cleanup) and now adapts it manually to the actual research: measure net gain and the
break-even threshold L* for guardrail configurations inside the refund process
(`dvarp/docs/guardrails-research-design.md`). Before rework, there must be a single map showing
which framework requirements the scaffold already covers, which are gaps, and which factory
additions are out of research scope. Without it, adaptation work is unmanaged rework (issue #5).

## What Changes

- New document `docs/traceability.md`: full matrix FR-1.1–1.24 + NFR-2.1–2.14 (38 positions)
  × coverage (component), verification (tests / verify.py steps), status
  (`covered / covered-heavy / gap / factory-extra`), plus the canon rows
  (5 tools, 3 agent models, 3 reaction policies, ≤16B, one-shot → dialogs).
- Four gaps from the matrix are filed as issues on the DVARP Board (models ≤16B + live pilot,
  §4 config matrix re-freeze, case packs from cycle-9 scenario spec, report gates
  G-tau / honest accounting), each with a presumed assignee.
- Recorded decisions: operator/payout contour stays (defer, revisit after gaps close);
  attacker isolation stays; factory openspec catalogs stay as history/format samples.

## Capabilities

### New Capabilities
- `traceability`: the maintained requirements-to-implementation matrix.

### Modified Capabilities
<!-- none: no code behavior changed in this change -->

## Impact

- Adds `docs/traceability.md` and `openspec/changes/polygon-alignment/`; no code changes,
  no config changes, frozen defense code untouched.
- Follow-up issues created on the board drive the next four substantive changes.
