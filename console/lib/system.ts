import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import { PYTHON, REPO } from "./paths";
import { isLoopback, readSettings } from "./settings";

function run(cmd: string, args: string[], timeout = 15000): Promise<{ ok: boolean; out: string }> {
  return new Promise((resolve) => {
    execFile(cmd, args, { cwd: REPO, timeout, maxBuffer: 4 << 20 }, (err, stdout, stderr) =>
      resolve({ ok: !err, out: (stdout || "") + (err ? stderr || err.message : "") }),
    );
  });
}

async function which(bin: string): Promise<string | null> {
  for (const dir of (process.env.PATH ?? "").split(path.delimiter)) {
    if (!dir) continue;
    const p = path.join(dir, bin);
    try {
      await fs.access(p, fs.constants.X_OK);
      return p;
    } catch {
      /* next */
    }
  }
  return null;
}

export type Health = {
  python: { ok: boolean; version: string };
  sandbox: { ok: boolean; path: string | null };
  freeze: { ok: boolean; detail: string };
  model: { configured: boolean; reachable: boolean; loopback: boolean; models: string[]; error?: string; baseUrl: string; name: string };
};

export async function health(): Promise<Health> {
  const s = await readSettings();
  const [py, bwrap, freeze, model] = await Promise.all([
    run(PYTHON, ["--version"]),
    which("bwrap"),
    run(PYTHON, ["-m", "darp_range.configs", "verify", "--dir", "configs"]),
    probeModel(s.baseUrl),
  ]);
  return {
    python: { ok: py.ok, version: py.out.trim() || "не найден" },
    sandbox: { ok: bwrap !== null, path: bwrap },
    freeze: { ok: freeze.ok && /\bOK\b/.test(freeze.out), detail: freeze.out.trim().split("\n").slice(-3).join("\n") },
    model: {
      configured: Boolean(s.baseUrl && s.model),
      loopback: isLoopback(s.baseUrl),
      baseUrl: s.baseUrl,
      name: s.model,
      ...model,
    },
  };
}

export async function probeModel(baseUrl: string): Promise<{ reachable: boolean; models: string[]; error?: string }> {
  if (!baseUrl) return { reachable: false, models: [] };
  try {
    const headers: Record<string, string> = {};
    if (process.env.RESEARCH_LLM_API_KEY) headers.Authorization = `Bearer ${process.env.RESEARCH_LLM_API_KEY}`;
    const r = await fetch(`${baseUrl.replace(/\/$/, "")}/models`, { headers, signal: AbortSignal.timeout(2500), cache: "no-store" });
    if (!r.ok) return { reachable: false, models: [], error: `HTTP ${r.status}` };
    const d = (await r.json()) as { data?: { id: string }[]; models?: { name: string }[] };
    const ids = d.data?.map((m) => m.id) ?? d.models?.map((m) => m.name) ?? [];
    return { reachable: true, models: [...new Set(ids)] };
  } catch (e) {
    return { reachable: false, models: [], error: e instanceof Error ? e.message : String(e) };
  }
}

export type Prompts = { agent: string; attacker: string };

let cached: Prompts | null = null;

/** System prompts as the polygon defines them, read from the code rather than copied. */
export async function prompts(): Promise<Prompts> {
  if (cached) return cached;
  const code =
    "import json\nfrom darp_range.agent.prompt import SYSTEM_PROMPT\nfrom darp_range.attackers.prompt import _INTRO\n" +
    "print(json.dumps({'agent': SYSTEM_PROMPT, 'attacker': _INTRO}))";
  const r = await run(PYTHON, ["-c", code]);
  if (!r.ok) return { agent: r.out, attacker: r.out };
  cached = JSON.parse(r.out) as Prompts;
  return cached;
}
