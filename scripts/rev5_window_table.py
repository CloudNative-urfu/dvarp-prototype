"""Ревизия 5 (report-validity): таблица окна вмешательства по всем k ∈ {0,1,2,3} с кластерным
bootstrap-ДИ разности IFR_k − IFR_rand_k (`metrics.temporal.intervention_window_table`).

Строки rogue-траекторий берутся из готовой диагностики (`rev4_stepshield.json → intervention_window
→ <детектор> → rows`, вариант --from-json) или заново из журналов прогона (--run, как в
`rev4_stepshield_diag.py`). Выделенного k нет; при ДИ, перекрывающем базлайн, — `not_informative`.

    python3 scripts/rev5_window_table.py --from-json runs/verify/rev4_stepshield.json \
        --out runs/verify/rev5_window_table.json
Офлайн, без сети.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _rows_from_json(path: Path) -> dict[str, list[dict]]:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {n: s.get("rows") or [] for n, s in (d.get("intervention_window") or {}).items()}


def _rows_from_run(run: Path, corpus: list[str]) -> dict[str, list[dict]]:
    from darp_range.metrics.temporal import intervention_window
    ind = set()
    for p in corpus:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                if e.get("kind") == "attack" and e["axes"].get("delivery") == "indirect_data":
                    ind.add(e["id"])
    out: dict[str, list[dict]] = {}
    for sd in sorted(p for p in run.glob("seed-*") if (p / "metrics/metrics.json").exists()):
        m = json.loads((sd / "metrics/metrics.json").read_text(encoding="utf-8"))
        for name, c in (m.get("configs") or {}).items():
            if not c.get("detect_only"):
                continue
            r = intervention_window(sd / "run", name, indirect_entries=ind)
            if r:
                out.setdefault(name, []).extend(dict(x, seed=sd.name) for x in r["rows"])
    return out


def markdown(res: dict) -> str:
    L = ["# Окно вмешательства: все k, кластерный bootstrap (ревизия 5)", "",
         "Кластер — сценарий (`entry_id`); правило: `informative` только если нижняя граница 95% ДИ "
         "разности IFR_k − IFR_rand_k > 0 и кластеров ≥ "
         f"{next(iter(res['detectors'].values()))['min_clusters'] if res['detectors'] else '—'}; "
         "иначе `not_informative`. Выделенного k нет.", "",
         "| детектор | k | строк | сценариев | IFR | IFR_rand | разность | 95% ДИ | статус |",
         "|---|---|---|---|---|---|---|---|---|"]
    f = lambda x: "—" if x is None else f"{x:.2f}"  # noqa: E731
    for name, t in res["detectors"].items():
        for k, r in t["by_k"].items():
            ci = r["diff_ci95"]
            L.append(f"| {name} | {k} | {t['rows']} | {t['clusters']} | {f(r['IFR'])} | "
                     f"{f(r['IFR_rand'])} | {f(r['diff'])} | "
                     f"{'—' if not ci else f'[{ci[0]:.2f}; {ci[1]:.2f}]'} | {r['status']} |")
    L += ["", f"Информативных k: {res['informative_any'] or 'нет'}. Данные — офлайн-траектории "
               "заглушки агента; seeds одной заглушки не независимы (кластер — сценарий)."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    from darp_range.metrics.temporal import intervention_window_table
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-json", type=Path)
    src.add_argument("--run", type=Path)
    ap.add_argument("--corpus", nargs="*", default=[str(ROOT / "corpus/darp_corpus_v1.jsonl")])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    rows = _rows_from_json(a.from_json) if a.from_json else _rows_from_run(a.run, a.corpus)
    det = {n: intervention_window_table(r) for n, r in sorted(rows.items())}
    res = {"source": str(a.from_json or a.run), "detectors": det,
           "informative_any": sorted({f"{n}:k={k}" for n, t in det.items()
                                      for k in t["informative_k"]}),
           "note": "replaces the single-k (k=3) claim of revision 4; offline stub trajectories"}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    md = markdown(res)
    a.out.with_suffix(".md").write_text(md, encoding="utf-8")
    print(md)
    return 0 if det else 2


if __name__ == "__main__":
    sys.exit(main())
