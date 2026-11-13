## MODIFIED Requirements

### Requirement: Requirements traceability matrix
The repository SHALL maintain `docs/traceability.md` — a matrix mapping every position of the
framework requirements (FR-1.1–1.24, NFR-2.1–2.14 from `dvarp/docs/guardrails_requirements.md`)
and the canon rows (5 tools, 3 agent models, 3 reaction policies, ≤16B, one-shot → dialogs) to
the scaffold: the covering component, the verification reference (tests / `scripts/verify.py`
steps), and a status of `covered`, `covered-heavy`, `gap` or `factory-extra`. Rows affected by a
canon deviation SHALL state the deviation and link to the «Отклонения канона» section of the
dvarp requirements.

Every `gap` row SHALL link to a board issue or carry an explicit out-of-scope justification.
Every `factory-extra` row SHALL carry a recorded decision (keep/defer/remove).

#### Scenario: Full coverage
- **WHEN** the matrix is reviewed against the requirements document
- **THEN** all 38 requirement positions and 5 canon rows are present with a status and a
  coverage/verification reference

#### Scenario: Canon deviations reflected
- **WHEN** the matrix is read for FR-1.12, FR-1.15, FR-1.16, FR-1.20 and FR-1.21
- **THEN** each row describes the implemented deviation (g_simple removed; analytic queue from
  the escalation share; predefined mode D sequences; goal `shift` removed) and the matrix links
  to the dvarp «Отклонения канона» section
