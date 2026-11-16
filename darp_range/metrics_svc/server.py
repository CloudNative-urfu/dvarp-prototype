"""HTTP-маршруты metrics-svc (issue #33). Телеметрия складывается в каталог расчёта в той же
раскладке, что пишет оркестратор (`manifest.json`, `run.json`, `results/`, `logs/`); на
сигнал завершения — проверка цепочек журналов, `metrics.compute` + `metrics.write`, гейты."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from darp_range.event_log import verify_chain
from darp_range.metrics import compute, load_run, write
from darp_range.metrics.extract import MetricsError
from darp_range.svckit import HTTPError, JSONHandler

from .gates import TAU_MAX, flows, g_tau, honest_accounting, interrupted_legal, lstar_check

MAX_BODY = 256 * 1024 * 1024
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")


def _name(s: Any, what: str) -> str:
    if not isinstance(s, str) or not _NAME.match(s) or ".." in s:
        raise HTTPError(400, "bad_name", f"{what}: {s!r}")
    return s


class State:
    def __init__(self, workdir: Path):
        self.workdir = Path(workdir)
        self.lock = threading.Lock()
        self.sources: dict[str, str] = {}


def gates(run_dir: Path, rep: dict[str, Any]) -> dict[str, Any]:
    """G-tau и honest accounting по конфигурациям, независимый пересчёт L*."""
    out: dict[str, Any] = {"tau": TAU_MAX, "configs": {}}
    for p in sorted((run_dir / "results").glob("*.json")):
        res = json.loads(p.read_text(encoding="utf-8"))
        legal: dict[str, list[str]] = {}
        for variant, f in flows(res):
            if variant != "base" or not f.get("log"):
                continue
            lines = (run_dir / f["log"]).read_text(encoding="utf-8").splitlines()
            legal.update(interrupted_legal(json.loads(x) for x in lines if x.strip()))
        out["configs"][p.stem] = {
            "g_tau": g_tau(legal),
            "honest_accounting": honest_accounting(res),
        }
    rows, bad, rule_bad = lstar_check(rep)
    out["l_star_independent"] = {
        "rows": rows,
        "mismatch": bad,
        "rule_violations": rule_bad,
        "pass": bool(rows) and not bad and not rule_bad,
    }
    out["pass"] = out["l_star_independent"]["pass"] and all(
        c["honest_accounting"]["pass"] for c in out["configs"].values()
    )
    out["marked_g_tau"] = sorted(
        n for n, c in out["configs"].items() if c.get("g_tau", {}).get("marked")
    )
    return out


def gates_md(g: dict[str, Any]) -> str:
    rows = [
        "# Гейты отчёта (metrics-svc)",
        "",
        f"G-tau: доля ложных прерываний на чистом трафике ≤ {g['tau']:.0%} (маркировка, "
        "не запрет); honest accounting: стороны раздельно, транспорт отдельной строкой.",
        "",
        "| config | legal | false interrupts | rate | CI95 | G-tau | honest accounting |",
        "| --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for n, c in sorted(g["configs"].items()):
        t, h = c.get("g_tau") or {}, c.get("honest_accounting") or {}
        ci = t.get("ci95") or [None, None]
        ci_s = "–" if ci[0] is None else f"[{ci[0]:.3f}, {ci[1]:.3f}]"
        rate = "–" if t.get("rate") is None else f"{t['rate']:.3f}"
        rows.append(
            f"| {n} | {t.get('n_legal', 0)} | {t.get('false_interrupts', 0)} | {rate} | "
            f"{ci_s} | {'marked' if t.get('marked') else 'ok'} | "
            f"{'PASS' if h.get('pass') else 'FAIL'} |"
        )
    return "\n".join(rows) + "\n"


class Handler(JSONHandler):
    max_body = MAX_BODY

    def route(self, method, parts, query, body):
        st: State = self.server.state
        if method == "GET" and parts == ["health"]:
            return 200, {"ok": True}, {}
        if method != "POST" or not isinstance(body, dict) or not parts or parts[0] != "runs":
            raise HTTPError(404, "not_found")
        with st.lock:
            if len(parts) == 1:
                return 200, self._start(st, body), {}
            rid = _name(parts[1], "run_id")
            d = st.workdir / rid
            if not d.is_dir() or len(parts) != 3:
                raise HTTPError(404, "no_run", rid)
            if parts[2] == "results":
                p = d / "results" / f"{_name(body.get('config'), 'config')}.json"
                p.parent.mkdir(exist_ok=True)
                p.write_text(json.dumps(body["doc"], ensure_ascii=False), encoding="utf-8")
                return 200, {"ok": True}, {}
            if parts[2] == "log":
                rel = [_name(x, "log path") for x in str(body.get("path", "")).split("/")]
                if not rel or rel[0] != "logs" or not rel[-1].endswith(".jsonl"):
                    raise HTTPError(400, "bad_log_path")
                p = d.joinpath(*rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a", encoding="utf-8") as fh:
                    fh.writelines(line + "\n" for line in body.get("lines") or ())
                return 200, {"ok": True}, {}
            if parts[2] == "finish":
                return 200, self._finish(st, rid, d, body), {}
        raise HTTPError(404, "not_found")

    def _start(self, st: State, body: dict) -> dict:
        rid = _name(body.get("run_id"), "run_id")
        d = st.workdir / rid
        if d.exists():
            raise HTTPError(409, "run_exists", rid)
        d.mkdir(parents=True)
        for name in ("manifest", "run"):
            (d / f"{name}.json").write_text(
                json.dumps(body[name], ensure_ascii=False), encoding="utf-8"
            )
        st.sources[rid] = str(body.get("source") or d)
        return {"ok": True, "run_id": rid}

    def _finish(self, st: State, rid: str, d: Path, body: dict) -> dict:
        bad = {}
        for p in sorted((d / "logs").rglob("*.jsonl")):
            ev = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
            err = verify_chain(ev)
            if err:
                bad[str(p.relative_to(d))] = err[:3]
        if bad:
            raise HTTPError(422, "telemetry_invalid", json.dumps(bad, ensure_ascii=False)[:2000])
        try:
            rd = load_run(d)
            rd.source = st.sources.get(rid, str(d))
            kw = {} if body.get("l_viol") is None else {"l_viol": tuple(body["l_viol"])}
            rep = compute(rd, reference=body["reference"], **kw)
        except MetricsError as exc:
            raise HTTPError(422, "metrics_error", str(exc)) from None
        out = d / "metrics"
        files = write(rep, out)
        g = gates(d, json.loads((out / "metrics.json").read_text(encoding="utf-8")))
        (out / "gates.json").write_text(
            json.dumps(g, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        (out / "gates.md").write_text(gates_md(g), encoding="utf-8")
        names = sorted(set(Path(p).name for p in files.values()) | {"gates.json", "gates.md"})
        return {
            "ok": True,
            "files": {n: (out / n).read_text(encoding="utf-8") for n in names},
            "gates_pass": g["pass"],
            "marked_g_tau": g["marked_g_tau"],
        }
