import { promises as fs } from "node:fs";
import path from "node:path";
import { CONFIGS } from "./paths";

export type GuardRef = { id: string; stages: string[] };

export type ConfigInfo = {
  name: string;
  family: string;
  controlOf: string | null;
  defender: string;
  notes: string;
  guards: GuardRef[];
  definition: string[];
  fingerprint: string;
  frozen: boolean;
};

export type LockInfo = { lockedAt: string; fingerprint: string; preregistered: boolean; frozenCount: number; codeModules: string[] };

async function readJson(file: string): Promise<Record<string, unknown> | null> {
  try {
    return JSON.parse(await fs.readFile(file, "utf8"));
  } catch {
    return null;
  }
}

export async function readLock(): Promise<LockInfo | null> {
  const d = await readJson(path.join(CONFIGS, "configs.lock.json"));
  if (!d) return null;
  return {
    lockedAt: String(d.locked_at ?? ""),
    fingerprint: String(d.lock_fingerprint ?? ""),
    preregistered: d.preregistered === true,
    frozenCount: Object.keys((d.configs as object) ?? {}).length,
    codeModules: Object.keys((d.defense_code as object) ?? {}),
  };
}

export async function listConfigNames(): Promise<string[]> {
  return (await listConfigs()).map((c) => c.name);
}

export async function listConfigs(): Promise<ConfigInfo[]> {
  const lock = await readJson(path.join(CONFIGS, "configs.lock.json"));
  const frozen = new Set(Object.keys((lock?.configs as object) ?? {}));
  let files: string[] = [];
  try {
    files = (await fs.readdir(CONFIGS)).filter((f) => /^g[\w]*\.json$/.test(f));
  } catch {
    return [];
  }
  const out: ConfigInfo[] = [];
  for (const f of files.sort()) {
    const d = await readJson(path.join(CONFIGS, f));
    if (!d || typeof d.name !== "string") continue;
    const guards = Array.isArray(d.guards) ? (d.guards as { id: string; stages?: string[] }[]) : [];
    out.push({
      name: d.name,
      family: String(d.family ?? ""),
      controlOf: typeof d.control_of === "string" ? d.control_of : null,
      defender: String(d.defender ?? ""),
      notes: String(d.notes ?? ""),
      guards: guards.map((g) => ({ id: g.id, stages: g.stages ?? [] })),
      definition: Array.isArray(d.operational_definition) ? (d.operational_definition as string[]) : [],
      fingerprint: String(d.fingerprint ?? ""),
      frozen: frozen.has(d.name),
    });
  }
  return out;
}
