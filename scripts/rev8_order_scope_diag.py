"""Ревизия 8 (`routing-neutrality-order-scope`): офлайн-диагностика «позиция вне заказа обращения».

Для каждого эпизода, где boundary-2r сам поставил ручную проверку (`create_refund` → `routed`), —
совпадает ли заказ маршрутизированной позиции (`order_items.order_id`) с заказом обращения
(`tickets.order_id`). Счёт раздельно: атаки (поток attacked, traffic=attack) и легальный трафик
(поток base, traffic=legal); прочие эпизоды (эталонные контроли пар, легальные в attacked-потоке)
публикуются отдельной строкой `other`.

Сетка: заглушка, основа v2r (boundary-2r + evaluator-2 + объявленные факты), корпуса v1 (dev) /
holdout_v2 / heldout_v3 (holdout) / trusted_v7 (holdout) × профили default/claims × g0/g_proc ×
seeds 7/8/9 — та же, что у `rev7_boundary_diag.py`, клиент `facts`.

Измерение: прогон `report_cli offline` внутри процесса, обёртка над `Executor._op_create_refund`
после завершения вызова читает (только SELECT, то же соединение и та же транзакция) заказ позиции и
обращения. Поведение исполнителя не меняется; правило привязки к заказу НЕ вводится (его нет в
документах кейса — вопрос владельцу).

Оговорка (review N4): таблица на заглушке измеряет состав корпуса и поведение заглушки, а не живой
модели; в ΔE·C_manual не переносится как оценка живого E.

    python3 scripts/rev8_order_scope_diag.py --out runs/rev8_order_scope --json X.json --md X.md
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

SEEDS = "7,8,9"
CONFIGS = "g0,g_proc"
PROFILES = ("default", "claims")
CORPORA = {
    "v1": {"corpus": None, "split": "dev"},
    "holdout_v2": {"corpus": "corpus/holdout_v2", "split": None},
    "heldout_v3": {"corpus": "corpus/heldout_v3", "split": "holdout"},
    "trusted_v7": {"corpus": "corpus/trusted_v7", "split": "holdout"},
}


def routed_scope(conn, ticket_id: str, order_item_id: int) -> dict:
    """Заказ обращения против заказа маршрутизированной позиции (только чтение)."""
    t = conn.execute("SELECT order_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    i = conn.execute("SELECT order_id FROM order_items WHERE id = ?", (order_item_id,)).fetchone()
    t_order, i_order = (t[0] if t else None), (i[0] if i else None)
    scope = ("no_ticket_order" if t_order is None else
             "in_order" if t_order == i_order else "out_of_order")
    return {"ticket_order": t_order, "item_order": i_order, "scope": scope}


class ScopeRecorder:
    """Контекст: перехват маршрутизированных `create_refund` (2r) и метаданных эпизода."""

    def __init__(self):
        self.rows: list[dict] = []
        self.tag: dict = {}

    def __enter__(self):
        from darp_range.executor.executor import Executor
        from darp_range.runner import flow as flow_mod
        self._ex_orig = Executor._op_create_refund
        self._ep_orig = flow_mod._EpisodeRun.__init__
        rec = self

        def op_create_refund(ex, role, **args):
            res = rec._ex_orig(ex, role, **args)
            if res.routed is not None and not res.replayed:
                row = routed_scope(ex._conn, ex.session.ticket_id, args["order_item_id"])
                row.update(dict(rec.tag), **getattr(ex, "_rev8_meta", {}),
                           episode_id=ex._episode_id, routed=res.routed)
                rec.rows.append(row)
            return res

        def ep_init(ep, ctx, **kw):
            rec._ep_orig(ep, ctx, **kw)
            ep.ex._rev8_meta = {"config": ctx.cfg.name, "variant": ctx.variant,
                                "traffic": ep.traffic, "entry_id": ep.entry["id"],
                                "seed": ctx.seed}

        Executor._op_create_refund = op_create_refund
        flow_mod._EpisodeRun.__init__ = ep_init
        return self

    def __exit__(self, *exc):
        from darp_range.executor.executor import Executor
        from darp_range.runner import flow as flow_mod
        Executor._op_create_refund = self._ex_orig
        flow_mod._EpisodeRun.__init__ = self._ep_orig
        return False


def unit(row: dict) -> str:
    if row.get("variant") == "attacked" and row.get("traffic") == "attack":
        return "attack"
    if row.get("variant") == "base" and row.get("traffic") == "legal":
        return "legal"
    return "other"


def summarize(rows: list[dict]) -> dict:
    tab: dict = defaultdict(lambda: Counter())
    by_entry: dict = defaultdict(Counter)
    for r in rows:
        u = unit(r)
        tab[(r.get("corpus"), u)][r["scope"]] += 1
        tab[("ALL", u)][r["scope"]] += 1
        if r["scope"] == "out_of_order":
            by_entry[f"{r.get('corpus')}:{r.get('entry_id')}:{u}"][r["routed"]] += 1
    out = {}
    for (c, u), cnt in sorted(tab.items()):
        n = sum(cnt.values())
        out.setdefault(c, {})[u] = {"routed": n, "in_order": cnt["in_order"],
                                    "out_of_order": cnt["out_of_order"],
                                    "no_ticket_order": cnt["no_ticket_order"],
                                    "out_of_order_share": round(cnt["out_of_order"] / n, 4)
                                    if n else None}
    return {"table": out, "out_of_order_by_entry": {k: dict(v) for k, v in sorted(by_entry.items())}}


def main(argv=None) -> int:
    from darp_range.report_cli.__main__ import main as report_main
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--json", type=Path, required=True)
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("--seeds", default=SEEDS)
    ap.add_argument("--corpora", default=",".join(CORPORA))
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    runs = {}
    with ScopeRecorder() as rec:
        for c in a.corpora.split(","):
            spec = CORPORA[c]
            for prof in PROFILES:
                name = f"{c}__{prof}__v2r"
                args = ["offline", "--out", str(a.out / name), "--seeds", a.seeds, "--configs",
                        CONFIGS, "--replay", "none", "--agent-profile", prof, "--foundation",
                        "v2r", "--legal-confirm", "facts"]
                if spec["corpus"]:
                    args += ["--corpus", spec["corpus"]]
                if spec["split"]:
                    args += ["--split", spec["split"]]
                rec.tag = {"corpus": c, "profile": prof}
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = report_main(args)
                (a.out / f"{name}.log").write_text(buf.getvalue(), encoding="utf-8")
                runs[name] = {"argv": ["report_cli", *args], "rc": rc}
                print(f"{name:34s} rc={rc} routed_so_far={len(rec.rows)}", flush=True)
    s = summarize(rec.rows)
    res = {"version": "rev8-order-scope-1", "foundation": "v2r", "legal_confirm": "facts",
           "seeds": a.seeds, "configs": CONFIGS, "profiles": list(PROFILES),
           "unit": "episode with server routing (create_refund -> manual_reviews by role server); "
                   "attack = attacked flow traffic=attack, legal = base flow traffic=legal, "
                   "other = reference controls / legal episodes inside the attacked flow",
           "caveat": "stub agent: measures corpus composition and stub behaviour, not the live "
                     "model; not an estimate of live E for dE*C_manual (review N4)",
           "rule_introduced": False,
           "owner_question": "the case documents contain no rule binding a refund to the order of "
                             "the ticket; whether routing of an item from another order of the same "
                             "customer is acceptable is a decision for the process owner",
           "runs": runs, **s, "rows": rec.rows,
           "ok": all(r["rc"] == 0 for r in runs.values()),
           "seconds": round(time.monotonic() - t0, 1)}
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.md:
        lines = ["# Ревизия 8: маршрутизация 2r по позиции вне заказа обращения (заглушка)", "",
                 "Единица — эпизод с серверной маршрутизацией. Оговорка: заглушка измеряет состав "
                 "корпуса и поведение заглушки, а не модели; в ΔE·C_manual не переносится (N4). "
                 "Правило привязки к заказу не вводилось: в документах кейса его нет — вопрос "
                 "владельцу.", "",
                 "| корпус | трафик | маршрутизировано | в заказе обращения | вне заказа | "
                 "без заказа у обращения | доля вне |", "|---|---|---|---|---|---|---|"]
        for c, d in res["table"].items():
            for u, v in d.items():
                lines.append(f"| {c} | {u} | {v['routed']} | {v['in_order']} | {v['out_of_order']} "
                             f"| {v['no_ticket_order']} | {v['out_of_order_share']} |")
        lines += ["", "Вне заказа по записям корпуса (причина маршрутизации → эпизоды):", ""]
        lines += [f"- `{k}`: {v}" for k, v in res["out_of_order_by_entry"].items()] or ["- нет"]
        a.md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(res["table"].get("ALL"), ensure_ascii=False))
    return 0 if res["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
