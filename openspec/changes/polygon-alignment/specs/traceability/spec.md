## ADDED Requirements

### Requirement: Requirements traceability matrix
The repository SHALL maintain `docs/traceability.md` — a matrix mapping every position of the
framework requirements (FR-1.1–1.24, NFR-2.1–2.14 from `dvarp/docs/guardrails_requirements.md`)
and the canon rows (5 tools, 3 agent models, 3 reaction policies, ≤8B, one-shot → dialogs) to
the scaffold: the covering component, the verification reference (tests / `scripts/verify.py`
steps), and a status of `covered`, `covered-heavy`, `gap` or `factory-extra`.

Every `gap` row SHALL link to a board issue or carry an explicit out-of-scope justification.
Every `factory-extra` row SHALL carry a recorded decision (keep/defer/remove).

#### Scenario: Full coverage
- **WHEN** the matrix is reviewed against the requirements document
- **THEN** all 38 requirement positions and 5 canon rows are present with a status and a
  coverage/verification reference

#### Scenario: Matrix stays current
- **WHEN** a follow-up change modifies behavior mapped in the matrix
- **THEN** that change updates the affected rows as part of its tasks

### Requirement: Scope decisions recorded
Decisions on factory extras SHALL be recorded in the matrix: the operator/payout contour
(shop-svc, pay-emu, payout-worker) is kept and deferred until the gap issues close; the
attacker isolation machinery is kept; factory openspec change catalogs remain as history and
format reference.

#### Scenario: Payout contour decision
- **WHEN** a follow-up change proposes removing or extending the operator/payout contour
- **THEN** it references the recorded decision and updates it if the decision changes
