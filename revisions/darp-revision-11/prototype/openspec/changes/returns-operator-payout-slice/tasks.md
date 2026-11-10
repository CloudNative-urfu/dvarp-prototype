## 1. Tasks

- [x] operator-decision-service Authenticated operator approve/reject in separate shop-svc process with in-transaction R1/R3/R4/ownership/state re-check; all operator-decision spec scenarios and mutants M1, M2 pass
- [x] payment-emulator-service Separate pay-emu process with own ledger, Idempotency-Key 400/409/422, fixed payee, charge cap, evaluator-owned faults; all payment-emulator scenarios and mutants M4, M5 pass
- [x] payout-reconciliation Payout outbox with key rr-<refund_request_id>, payout-worker process, unknown state, monotonic reconciliation and customer status; all payout-reconciliation scenarios and mutants M3, M6, M7 pass
- [x] returns-slice-e2e scripts/shop_slice.py up/reset/smoke/down, LLM-free end-to-end path, 10-scenario failure matrix judged by independent read-only oracle with planted-double-payout negative control, rev10_verify PASS, smoke+mutants ≤ 300 s wall
- [x] broker-fail-closed IPC wait timeout to ipc_protocol_error, 64 KiB stderr cap, Runner live A fail-closed without factory; broker-fail-closed scenarios and mutants M8, M9 pass

## Notes

Test details are in the spec scenarios above. Order: RED tests and mutants first, then implementation, then GREEN. Save to `artifacts/` the CLI validate output, the smoke and oracle JSON, the mutant report, the JSONL request traces, and the up/reset/restart command logs.
