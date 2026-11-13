import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, promises as fs, createWriteStream, type WriteStream } from "node:fs";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { ACTIONS, NAME_RE, SEEDS_RE, actionById, type ActionId } from "./actions";
import { JOBS_DIR, PYTHON, REPO, RUNS, runDir } from "./paths";
import { listConfigNames } from "./configs";
import { LOOPBACK_ERROR, isLoopback, modelEnv, readSettings } from "./settings";

export type JobStatus = "running" | "ok" | "failed" | "stopped";

export type JobMeta = {
  id: string;
  action: ActionId;
  title: string;
  params: Record<string, string>;
  command: string;
  startedAt: number;
  endedAt?: number;
  status: JobStatus;
  exitCode?: number | null;
  run?: string;
  usesModel: boolean;
};

type Live = {
  meta: JobMeta;
  proc: ChildProcess;
  log: WriteStream;
  output: string;
  listeners: Set<(e: JobEvent) => void>;
};
export type JobEvent = { type: "log"; text: string } | { type: "end"; meta: JobMeta };

// survives dev hot reloads
const g = globalThis as unknown as { __darpJobs?: Map<string, Live> };
const live: Map<string, Live> = (g.__darpJobs ??= new Map());

export class JobError extends Error {
  constructor(
    message: string,
    public field?: string,
  ) {
    super(message);
  }
}

type Built = { argv: string[]; run?: string; usesModel: boolean };

async function build(action: ActionId, p: Record<string, string>): Promise<Built> {
  const py = (...args: string[]) => [PYTHON, ...args];
  const name = () => {
    const n = (p.name ?? "").trim();
    if (!NAME_RE.test(n)) throw new JobError("Имя каталога: латиница в нижнем регистре, цифры, - и _", "name");
    if (existsSync(path.join(RUNS, n))) throw new JobError(`runs/${n} уже есть, выберите другое имя`, "name");
    return n;
  };
  const model = () => {
    if (p.model !== "stub" && p.model !== "live") throw new JobError("Модель: заглушка или живая", "model");
    return p.model;
  };
  const configs = async () => {
    const raw = (p.configs ?? "").trim();
    if (!raw) return [];
    const known = new Set(await listConfigNames());
    const list = raw.split(",").map((s) => s.trim()).filter(Boolean);
    const bad = list.filter((c) => !known.has(c));
    if (bad.length) throw new JobError(`Нет таких конфигураций: ${bad.join(", ")}`, "configs");
    return ["--configs", [...new Set(list)].join(",")];
  };
  const mode = () => {
    if (p.attackMode !== "D" && p.attackMode !== "A") throw new JobError("Режим атакующего: D или A", "attackMode");
    return p.attackMode;
  };
  switch (action) {
    case "configs-verify":
      return { argv: py("-m", "darp_range.configs", "verify", "--dir", "configs"), usesModel: false };
    case "agent-demo": {
      const m = model();
      const n = name();
      const out = `runs/${n}/${m === "live" ? "agent_live" : "agent_demo"}.json`;
      return {
        argv: py("-m", "darp_range.agent_demo", m === "live" ? "live" : "demo", "--out", out),
        run: n,
        usesModel: m === "live",
      };
    }
    case "attackers-demo": {
      const m = model();
      const n = name();
      return {
        argv: py("-m", "darp_range.attackers", "demo", "--model", m, "--out", `runs/${n}/attackers.json`),
        run: n,
        usesModel: m === "live",
      };
    }
    case "offline": {
      const seeds = (p.seeds ?? "").replace(/\s+/g, "");
      if (!SEEDS_RE.test(seeds)) throw new JobError("Seed'ы: целые числа через запятую, до пяти", "seeds");
      const c = await configs();
      const m = mode();
      const n = name();
      return {
        argv: py("-m", "darp_range.report_cli", "offline", "--out", `runs/${n}`, "--seeds", seeds, ...c, "--attack-mode", m),
        run: n,
        usesModel: false,
      };
    }
    case "verify-run": {
      const r = (p.run ?? "").trim();
      const dir = runDir(r);
      if (!dir || !existsSync(path.join(dir, "summary.json"))) throw new JobError("Выберите готовый прогон", "run");
      return { argv: py("-m", "darp_range.report_cli", "verify", "--out", `runs/${r}`), run: r, usesModel: false };
    }
    case "shop-smoke": {
      const n = name();
      return { argv: py("scripts/shop_slice.py", "smoke", "--out", `runs/${n}`), run: n, usesModel: false };
    }
    case "live": {
      const m = mode();
      const c = await configs();
      const max = Number(p.maxRequests);
      if (!Number.isInteger(max) || max < 4 || max > 200) throw new JobError("Бюджет: целое от 4 до 200", "maxRequests");
      const n = name();
      return {
        argv: py("-m", "darp_range.report_cli", "live", "--out", `runs/${n}`, "--attack-mode", m, ...c, "--max-requests", String(max)),
        run: n,
        usesModel: true,
      };
    }
  }
}

const quote = (a: string) => (/^[\w@%+=:,./-]+$/.test(a) ? a : `'${a.replace(/'/g, `'\\''`)}'`);

export async function startJob(actionId: string, params: Record<string, string>): Promise<JobMeta> {
  const def = actionById(actionId);
  if (!def) throw new JobError("Неизвестный путь");
  const clean: Record<string, string> = {};
  for (const pd of def.params) if (typeof params[pd.key] === "string") clean[pd.key] = params[pd.key];
  const built = await build(def.id, clean);
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUNBUFFERED: "1" };
  for (const k of Object.keys(env)) if (k.startsWith("RESEARCH_LLM_") && k !== "RESEARCH_LLM_API_KEY") delete env[k];
  if (built.usesModel) {
    const s = await readSettings();
    if (!s.baseUrl || !s.model) throw new JobError("Укажите адрес и имя модели в настройках", "model");
    // settings.json may predate the loopback check or be edited by hand
    if (!isLoopback(s.baseUrl)) throw new JobError(`Адрес модели в настройках: ${LOOPBACK_ERROR}`, "model");
    Object.assign(env, modelEnv(s));
  } else {
    delete env.RESEARCH_LLM_API_KEY;
  }
  const id = `${Date.now().toString(36)}-${randomBytes(3).toString("hex")}`;
  await fs.mkdir(JOBS_DIR, { recursive: true });
  if (built.run && def.id !== "verify-run") await fs.mkdir(path.join(RUNS, built.run), { recursive: true });
  const meta: JobMeta = {
    id,
    action: def.id,
    title: def.title,
    params: clean,
    command: built.argv.map(quote).join(" "),
    startedAt: Date.now(),
    status: "running",
    run: built.run,
    usesModel: built.usesModel,
  };
  const log = createWriteStream(path.join(JOBS_DIR, `${id}.log`));
  const proc = spawn(built.argv[0], built.argv.slice(1), {
    cwd: REPO,
    env,
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const entry: Live = { meta, proc, log, output: "", listeners: new Set() };
  live.set(id, entry);
  await saveMeta(meta);
  const emit = (e: JobEvent) => entry.listeners.forEach((fn) => fn(e));
  const onData = (b: Buffer) => {
    const text = b.toString("utf8");
    log.write(text);
    entry.output += text;
    emit({ type: "log", text });
  };
  proc.stdout?.on("data", onData);
  proc.stderr?.on("data", onData);
  const finish = (code: number | null, err?: Error) => {
    if (meta.endedAt) return;
    if (err) onData(Buffer.from(`\n${err.message}\n`));
    meta.endedAt = Date.now();
    meta.exitCode = code;
    if (meta.status === "running") meta.status = code === 0 ? "ok" : "failed";
    log.end();
    void saveMeta(meta).then(() => {
      emit({ type: "end", meta });
      live.delete(id);
    });
  };
  proc.on("error", (err) => finish(null, err));
  proc.on("close", (code) => finish(code));
  return meta;
}

export function stopJob(id: string): boolean {
  const e = live.get(id);
  if (!e || e.meta.status !== "running" || !e.proc.pid) return false;
  e.meta.status = "stopped";
  try {
    process.kill(-e.proc.pid, "SIGTERM");
  } catch {
    e.proc.kill("SIGTERM");
  }
  return true;
}

async function saveMeta(meta: JobMeta) {
  await fs.writeFile(path.join(JOBS_DIR, `${meta.id}.json`), JSON.stringify(meta));
}

export async function getJob(id: string): Promise<JobMeta | null> {
  if (!/^[a-z0-9]+-[0-9a-f]{6}$/.test(id)) return null;
  const l = live.get(id);
  if (l) return l.meta;
  try {
    const meta = JSON.parse(await fs.readFile(path.join(JOBS_DIR, `${id}.json`), "utf8")) as JobMeta;
    // a job still marked running without a process belongs to a previous server: it was lost
    if (meta.status === "running") return { ...meta, status: "stopped" };
    return meta;
  } catch {
    return null;
  }
}

export async function listJobs(limit = 40): Promise<JobMeta[]> {
  let files: string[] = [];
  try {
    files = (await fs.readdir(JOBS_DIR)).filter((f) => f.endsWith(".json"));
  } catch {
    return [];
  }
  const metas = await Promise.all(files.map((f) => getJob(f.slice(0, -5))));
  return metas
    .filter((m): m is JobMeta => m !== null)
    .sort((a, b) => b.startedAt - a.startedAt)
    .slice(0, limit);
}

export async function readLog(id: string): Promise<string> {
  try {
    return await fs.readFile(path.join(JOBS_DIR, `${id}.log`), "utf8");
  } catch {
    return "";
  }
}

/** Output so far plus a subscription to the rest, taken in one tick so nothing is lost
 * between them; null when the job is not running in this server. */
export function attach(id: string, fn: (e: JobEvent) => void): { output: string; detach: () => void } | null {
  const e = live.get(id);
  if (!e) return null;
  e.listeners.add(fn);
  return { output: e.output, detach: () => e.listeners.delete(fn) };
}

export const KNOWN_ACTIONS = ACTIONS.map((a) => a.id);
