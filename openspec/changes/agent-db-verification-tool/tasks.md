# Tasks

- [x] db-verification-tool Variant 2 of canon request 001: `read_order` returns `purchase_history` (all session-customer orders with items and refund flags, R6-scoped) and explicit `note_provenance`; no sixth tool, FR-1.11 count unchanged; request file status updated
- [x] v3-audit-writes `request_info` writes `info_requests.question_text` (validated, ≤ 500 chars); `create_refund` writes `ops_ledger.detail_json` and status `approved` when covered by trusted operator approval; `tickets.closed_at` written at every finalization; all writes are v3-conditional (v1/v2 behavior byte-identical)
- [x] operator-resolution New executor operation `resolve_review` for role `operator`: updates only `manual_reviews.decided_at/decision/decided_by`, closes the escalated ticket (`resolved` on approved, `refused` on rejected), ledger entry with decision payload; other roles and bad decisions denied; repeated resolution denied; requires schema v3
- [x] authorizer-depth Authorizer allows UPDATE `manual_reviews` only for decided columns; `tickets` updatable columns gain `closed_at`; TEMP trigger on v3 aborts any other `manual_reviews` change; DELETE/INSERT still forbidden
- [x] tool-spec-and-prompt `refund-tools-2` (read_order description covers purchase_history), `refund-agent-prompt-5` (verification step), toolbox passes `question_text` to the executor
- [x] access-rights-doc `darp_range/state_store/README.md` gains "Права доступа" section (session identity, roles, operator, append-only, trusted tables, v3-conditional writes)
- [x] pr-opened PR to `develop`, `Closes #39`, issue #39 board status updated

## Notes

Verification: `ruff check .`; `tests.test_issue39_db_tools` (18 tests) green;
neighbor suites (`test_executor`, `test_tools`, `test_revision8_append_only`,
`test_review_routing_v2r`, `test_agent`) green; `scripts/dev_smoke.py` green.
