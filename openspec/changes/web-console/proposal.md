## Why

Every polygon path is a terminal command, and its results live in `report.md`, `summary.json` and raw event logs. The team wants to launch the paths and read the results in a browser, locally, without touching the monolith (#24).

## What Changes

- New `console/` package: Next.js (TypeScript, App Router), served on 127.0.0.1.
- A fixed whitelist of seven paths (freeze check, agent demo, attacker demo, offline run, run re-check, shop contour, live run). The server builds argv from the whitelist and validated parameters; the client never sends a command line.
- Jobs run as child processes in the repository root; output is streamed to the browser (server-sent events) and kept with metadata in `console/.data/jobs/`. A running job can be stopped.
- Read-only views over existing files: run list, per-config metrics (V, ΔV with CI, L*, Q, legal share, C_ep, break-even status, Pareto, inefficient), self-checks, live model budget, attacker dialogs with evaluator verdicts and attacker perception, shop scenarios, agent and attacker demo results, frozen configurations, agent and attacker prompts read from code.
- Model settings (endpoint, name, size, extra body) in a gitignored file, passed to live jobs as `RESEARCH_LLM_*`; the API key comes only from the server environment; offline jobs run with `RESEARCH_LLM_*` removed.

## Capabilities

### New Capabilities
- `web-console`: local browser console over the polygon CLIs and run files.

### Modified Capabilities
- (none)

## Impact

New directory `console/` only; `.gitignore` there covers `node_modules`, `.next`, `.data`. No Python code, configuration, corpus or frozen file changes. Ruff and commit-msg CI are unaffected.
