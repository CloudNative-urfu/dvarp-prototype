## 1. Tasks

- [x] matrix `docs/traceability.md`: all 38 requirement positions (FR-1.1–1.24, NFR-2.1–2.14) with coverage, verification reference and status; canon rows; factory-extra decisions; gap-to-issue table
- [x] issues Four gap issues filed on the DVARP Board (models + live pilot, §4 config matrix, case packs, campaign gates) with presumed assignees
- [ ] review Matrix reviewed by the issue author; follow-up issues reformulated/reassigned as needed
- [ ] keep-alive Convention: every follow-up change updates the matrix rows it touches (recorded in each change's tasks.md)

## Notes

Acceptance: 100% requirement positions in the matrix; every `gap` row has an issue link or an
explicit out-of-scope justification; every `factory-extra` row has a recorded decision. No code
changes in this change; frozen defense code untouched. The matrix will be re-validated by
`scripts/verify.py` runs once models are wired (gap issues) — full verification remains deferred
per issue #2 justification until the scaffold is functional end-to-end.
