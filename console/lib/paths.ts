import path from "node:path";

// The console lives in <repo>/console; DARP_REPO overrides for a checkout elsewhere.
export const REPO = path.resolve(process.env.DARP_REPO ?? path.join(process.cwd(), ".."));
export const RUNS = path.join(REPO, "runs");
export const CONFIGS = path.join(REPO, "configs");
export const DATA = path.join(process.cwd(), ".data");
export const JOBS_DIR = path.join(DATA, "jobs");
export const PYTHON = process.env.DARP_PYTHON ?? "python3";

/** Resolve a run directory name inside runs/, refusing anything that escapes it. */
export function runDir(name: string): string | null {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(name) || name.includes("..")) return null;
  return path.join(RUNS, name);
}
