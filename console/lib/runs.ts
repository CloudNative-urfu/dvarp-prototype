import { promises as fs } from "node:fs";
import path from "node:path";
import { RUNS, runDir } from "./paths";

export type RunKind = "offline" | "live" | "shop" | "agent" | "attackers" | "empty";

export type RunListItem = { name: string; kind: RunKind; ok: boolean | null; mtime: number; label: string };

export type Check = { name: string; pass: boolean };

export type ConfigMetrics = {
  name: string;
  role: string;
  status: string;
  formulaStatus: string;
  V: number | null;
  dV: number | null;
  dVci: [number, number] | null;
  Q: number | null;
  R: number | null;
  E: number | null;
  Lstar: number | null;
  Cep: number | null;
  legalShare: number | null;
  reasons: string[];
};

export type LiveMeter = {
  requests: number;
  maxRequests: number;
  tokensIn: number;
  tokensOut: number;
  elapsed: number;
  failed: number;
  limitHit: boolean;
  model: string;
  paramsB: number | null;
};

export type SmokeScenario = { scenario: string; ok: boolean; error: string | null; checks: Check[] };

export type DemoRow = { label: string; values: [string, string][] };
export type DemoTranscript = { id: string; outcome: string; messages: Message[] };
export type DemoReport = { checks: Check[]; rows: DemoRow[]; transcripts: DemoTranscript[] };

export type RunDetail = {
  name: string;
  kind: RunKind;
  ok: boolean | null;
  mtime: number;
  mode?: string;
  seeds?: number[];
  claimLevels?: Record<string, string>;
  checks: Check[];
  configs: ConfigMetrics[];
  pareto: string[];
  inefficient: string[];
  live?: LiveMeter;
  smoke?: SmokeScenario[];
  demo?: DemoReport;
  hasDialogs: boolean;
};

async function json(file: string): Promise<Record<string, unknown> | null> {
  try {
    return JSON.parse(await fs.readFile(file, "utf8"));
  } catch {
    return null;
  }
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
// report_cli and attackers write {name, pass}; agent_demo writes {check, passed}
const checks = (v: unknown): Check[] =>
  Array.isArray(v) ? v.map((c) => ({ name: String(c?.name ?? c?.check ?? ""), pass: c?.pass === true || c?.passed === true })) : [];

async function exists(p: string) {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

async function kindOf(dir: string): Promise<{ kind: RunKind; ok: boolean | null; label: string }> {
  const s = await json(path.join(dir, "summary.json"));
  if (s) {
    const mode = String(s.mode ?? "");
    const isLive = mode === "live" || (await exists(path.join(dir, "live.json")));
    const n = Object.keys((s.configs as object) ?? {}).length;
    return { kind: isLive ? "live" : "offline", ok: s.ok === true, label: `${n} конфиг.` };
  }
  const smoke = await json(path.join(dir, "smoke.json"));
  if (smoke) {
    const sc = Array.isArray(smoke.scenarios) ? smoke.scenarios.length : 0;
    return { kind: "shop", ok: smoke.ok === true, label: `${sc} сценариев` };
  }
  for (const [f, kind] of [
    ["agent_demo.json", "agent"],
    ["agent_live.json", "agent"],
    ["attackers.json", "attackers"],
  ] as const) {
    const d = await json(path.join(/*turbopackIgnore: true*/ dir, f));
    if (d) {
      const c = checks(d.checks);
      return { kind, ok: c.length ? c.every((x) => x.pass) : null, label: f.includes("live") ? "живая модель" : "проверки" };
    }
  }
  return { kind: "empty", ok: null, label: "нет итогов" };
}

export async function listRuns(): Promise<RunListItem[]> {
  let names: string[] = [];
  try {
    names = await fs.readdir(RUNS);
  } catch {
    return [];
  }
  const items: RunListItem[] = [];
  for (const name of names) {
    const dir = runDir(name);
    if (!dir) continue;
    const st = await fs.stat(dir).catch(() => null);
    if (!st?.isDirectory()) continue;
    const k = await kindOf(dir);
    items.push({ name, mtime: st.mtimeMs, ...k });
  }
  return items.sort((a, b) => b.mtime - a.mtime);
}

function configRows(s: Record<string, unknown>): ConfigMetrics[] {
  const cfg = (s.configs as Record<string, Record<string, unknown>>) ?? {};
  return Object.entries(cfg).map(([name, c]) => {
    const ci = Array.isArray(c.dV_ci95) && c.dV_ci95.length === 2 ? (c.dV_ci95 as [number, number]) : null;
    return {
      name,
      role: String(c.role ?? (c.formula_status === "reference" ? "baseline" : "")),
      status: String(c.status ?? c.formula_status ?? ""),
      formulaStatus: String(c.formula_status ?? ""),
      V: num(c.V),
      dV: num(c.dV),
      dVci: ci,
      Q: num(c.Q),
      R: num(c.R),
      E: num(c.E),
      Lstar: num(c.L_star),
      Cep: num(c.C_ep),
      legalShare: num(c.legal_completed_share),
      reasons: Array.isArray(c.unresolved_reasons) ? (c.unresolved_reasons as string[]) : [],
    };
  });
}

export async function getRun(name: string): Promise<RunDetail | null> {
  const dir = runDir(name);
  if (!dir) return null;
  const st = await fs.stat(dir).catch(() => null);
  if (!st?.isDirectory()) return null;
  const k = await kindOf(dir);
  const detail: RunDetail = {
    name,
    kind: k.kind,
    ok: k.ok,
    mtime: st.mtimeMs,
    checks: [],
    configs: [],
    pareto: [],
    inefficient: [],
    hasDialogs: false,
  };
  const s = await json(path.join(dir, "summary.json"));
  if (s) {
    detail.mode = String(s.mode ?? "");
    detail.seeds = Array.isArray(s.seeds) ? (s.seeds as number[]) : undefined;
    detail.claimLevels = (s.claim_levels as Record<string, string>) ?? undefined;
    detail.checks = checks(s.checks);
    detail.configs = configRows(s);
    detail.pareto = Array.isArray(s.pareto_front) ? (s.pareto_front as string[]) : [];
    detail.inefficient = Array.isArray(s.inefficient) ? (s.inefficient as string[]) : [];
    detail.hasDialogs = (await attackedLogs(dir)).length > 0;
  }
  const l = await json(path.join(dir, "live.json"));
  if (l) {
    const meter = (l.meter as Record<string, Record<string, unknown>>) ?? {};
    const used = meter.used ?? {};
    const budget = meter.budget ?? {};
    const model = (l.model as Record<string, unknown>) ?? {};
    detail.live = {
      requests: num(used.requests) ?? 0,
      maxRequests: num(budget.max_requests) ?? 0,
      tokensIn: num(used.tokens_in) ?? 0,
      tokensOut: num(used.tokens_out) ?? 0,
      failed: num(used.failed) ?? 0,
      elapsed: num((meter as Record<string, unknown>).elapsed_s) ?? 0,
      limitHit: (meter as Record<string, unknown>).limit_hit === true,
      model: String(model.name ?? ""),
      paramsB: num(model.params_b),
    };
  }
  const smoke = await json(path.join(dir, "smoke.json"));
  if (smoke && Array.isArray(smoke.scenarios)) {
    detail.smoke = (smoke.scenarios as Record<string, unknown>[]).map((x) => ({
      scenario: String(x.scenario ?? ""),
      ok: x.ok === true,
      error: typeof x.error === "string" ? x.error : null,
      checks: checks(x.checks),
    }));
  }
  for (const f of ["agent_demo.json", "agent_live.json", "attackers.json"]) {
    const d = await json(path.join(/*turbopackIgnore: true*/ dir, f));
    if (d) detail.demo = demoReport(d, f);
  }
  return detail;
}

function summaryRow(label: string, sm: Record<string, unknown>): DemoRow {
  const outcomes = (sm.attack_outcomes as Record<string, number>) ?? {};
  const viol = Array.isArray(sm.violations) ? sm.violations.length : 0;
  return {
    label,
    values: [
      ["нарушений", String(viol)],
      ["клиенты обслужены", `${sm.controls_completed ?? 0} из ${sm.controls ?? 0}`],
      ["исходы атак", Object.entries(outcomes).map(([k, n]) => `${k} ${n}`).join(", ") || "нет атак"],
    ],
  };
}

function demoReport(d: Record<string, unknown>, file: string): DemoReport {
  const rep: DemoReport = { checks: checks(d.checks), rows: [], transcripts: [] };
  const runs = d.runs as Record<string, Record<string, unknown>> | undefined;
  if (file === "attackers.json" && runs) {
    for (const [k, r] of Object.entries(runs)) {
      rep.rows.push({
        label: k,
        values: [
          ["остановка", String(r.stopped ?? "")],
          ["попыток", String(Array.isArray(r.attempts) ? r.attempts.length : 0)],
          ["нарушение протокола", r.violation ? String(r.violation) : "нет"],
        ],
      });
    }
  } else if (runs) {
    for (const [k, r] of Object.entries(runs)) if (r.summary) rep.rows.push(summaryRow(k, r.summary as Record<string, unknown>));
  } else if (d.summary) {
    rep.rows.push(summaryRow("живая модель", d.summary as Record<string, unknown>));
    const eps = (d.episodes as Record<string, Record<string, unknown>>) ?? {};
    for (const [id, e] of Object.entries(eps)) {
      const tr = Array.isArray(e.transcript) ? (e.transcript as unknown[][]) : [];
      rep.transcripts.push({
        id,
        outcome: String(e.outcome ?? e.task_status ?? ""),
        messages: tr.map(([role, text]) => ({
          from: role !== "to_defender" ? "agent" : id.startsWith("atk") ? "attacker" : "client",
          text: String(text ?? ""),
        })),
      });
    }
  }
  return rep;
}

async function attackedLogs(dir: string): Promise<{ seed: string; cfg: string; file: string }[]> {
  const out: { seed: string; cfg: string; file: string }[] = [];
  const seeds = (await fs.readdir(dir).catch(() => [] as string[])).filter((s) => s.startsWith("seed-"));
  for (const seed of seeds) {
    const logs = path.join(dir, seed, "run", "logs");
    for (const cfg of await fs.readdir(logs).catch(() => [] as string[])) {
      for (const f of await fs.readdir(path.join(logs, cfg)).catch(() => [] as string[])) {
        if (f.endsWith("-attacked.jsonl")) out.push({ seed, cfg, file: path.join(logs, cfg, f) });
      }
    }
  }
  return out.sort((a, b) => a.file.localeCompare(b.file));
}

export type Message = { from: "attacker" | "client" | "agent"; text: string; closed?: boolean };

export type Episode = {
  id: string;
  seed: string;
  config: string;
  traffic: "attack" | "legal";
  attempt: number;
  messages: Message[];
  verdict: string | null;
  seen: string | null;
  believedSuccess: boolean | null;
  tactics: string[];
  notes: string[];
  repeatsPrevious: boolean;
};

const MAX_EPISODES = 400;

export async function getDialogs(name: string): Promise<Episode[] | null> {
  const dir = runDir(name);
  if (!dir) return null;
  const episodes: Episode[] = [];
  for (const { seed, cfg, file } of await attackedLogs(dir)) {
    const byId = new Map<string, Episode>();
    const text = await fs.readFile(file, "utf8").catch(() => "");
    for (const line of text.split("\n")) {
      if (!line) continue;
      let e: Record<string, unknown>;
      try {
        e = JSON.parse(line);
      } catch {
        continue;
      }
      const d = (e.data as Record<string, unknown>) ?? {};
      const comp = e.component;
      const kind = e.kind;
      const epId = String(comp === "evaluator" ? (d.episode_id ?? e.episode_id ?? "") : (e.episode_id ?? ""));
      if (!epId) continue;
      const get = () => {
        let ep = byId.get(epId);
        if (!ep) {
          const m = /^(.+)-a(\d+)$/.exec(epId);
          ep = {
            id: epId,
            seed,
            config: cfg,
            traffic: epId.includes("attack") ? "attack" : "legal",
            attempt: m ? Number(m[2]) : 1,
            messages: [],
            verdict: null,
            seen: null,
            believedSuccess: null,
            tactics: [],
            notes: [],
            repeatsPrevious: false,
          };
          byId.set(epId, ep);
        }
        return ep;
      };
      if (comp === "channel" && kind === "message" && typeof d.text === "string") {
        const ep = get();
        const toDefender = d.direction === "to_defender";
        ep.messages.push({
          from: toDefender ? (ep.traffic === "attack" ? "attacker" : "client") : "agent",
          text: d.text,
          closed: d.closed === true,
        });
      } else if (comp === "evaluator") {
        const v = d.outcome ?? d.task_status;
        if (typeof v === "string") get().verdict = v;
      } else if (comp === "attacker" && kind === "attempt" && d.phase === "end") {
        const ep = get();
        ep.seen = typeof d.seen === "string" ? d.seen : null;
        ep.believedSuccess = d.believed_success === true;
      } else if (comp === "attacker" && kind === "message_composed") {
        const ep = get();
        if (typeof d.tactic === "string" && d.sent) ep.tactics.push(d.tactic);
        if (typeof d.note === "string" && d.note) ep.notes.push(d.note);
      }
    }
    const firstByBase = new Map<string, string>();
    for (const ep of [...byId.values()].sort((a, b) => a.id.localeCompare(b.id))) {
      const base = ep.id.replace(/-a\d+$/, "");
      const first = ep.messages[0]?.text ?? "";
      if (ep.attempt > 1 && firstByBase.get(base) === first) ep.repeatsPrevious = true;
      if (ep.attempt === 1) firstByBase.set(base, first);
      episodes.push(ep);
      if (episodes.length >= MAX_EPISODES) return episodes;
    }
  }
  return episodes;
}
