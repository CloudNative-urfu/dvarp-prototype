# DARP console

Local web console for the polygon: launch every user path, follow the process output live,
read run results (metrics, Pareto, dialogs with evaluator verdicts) and the frozen configurations.
The polygon itself is unchanged: the console calls the same CLIs from a fixed whitelist
(`lib/actions.ts`) and reads files under `runs/` and `configs/`.

## Run

Requirements: Node.js 20+, and the polygon's own environment on `PATH` (Python 3.11+, the
repository requirements; `bwrap` for live mode A).

```sh
cd console
npm ci
npm run build && npm run start   # http://127.0.0.1:3100
# or: npm run dev
```

The server binds to 127.0.0.1 only. `DARP_REPO` points to another checkout, `DARP_PYTHON`
selects the interpreter (default `python3`).

## Live model

Section "Модель" stores the OpenAI-compatible endpoint, model name, size and extra body in
`console/.data/settings.json` (gitignored) and passes them to live jobs as `RESEARCH_LLM_*`.
The API key is never stored: set `RESEARCH_LLM_API_KEY` in the console server's environment
if the endpoint needs one. Offline jobs run with all `RESEARCH_LLM_*` removed.

## Paths

| Path | Command |
| --- | --- |
| Freeze check | `python3 -m darp_range.configs verify --dir configs` |
| Refund agent | `python3 -m darp_range.agent_demo demo\|live --out runs/<name>/…` |
| Attacker | `python3 -m darp_range.attackers demo --model stub\|live --out runs/<name>/attackers.json` |
| Offline run | `python3 -m darp_range.report_cli offline --out runs/<name> --seeds … --configs … --attack-mode D\|A` |
| Run re-check | `python3 -m darp_range.report_cli verify --out runs/<run>` |
| Shop contour | `python3 scripts/shop_slice.py smoke --out runs/<name>` |
| Live run | `python3 -m darp_range.report_cli live --out runs/<name> --attack-mode A\|D --configs … --max-requests N` |

Every parameter is validated on the server (known configuration names, seed list, run
directory name that does not exist yet). Job metadata and full output are kept in
`console/.data/jobs/`.

## Checks

`npm run lint`, `npm run typecheck`, `npm run build`.
