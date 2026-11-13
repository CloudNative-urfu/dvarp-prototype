import { promises as fs } from "node:fs";
import path from "node:path";
import { DATA } from "./paths";

export type ModelSettings = {
  baseUrl: string;
  model: string;
  paramsB: string;
  extraBody: string;
};

const FILE = path.join(DATA, "settings.json");
const EMPTY: ModelSettings = { baseUrl: "", model: "", paramsB: "", extraBody: "" };

export async function readSettings(): Promise<ModelSettings> {
  try {
    const raw = JSON.parse(await fs.readFile(FILE, "utf8"));
    return { ...EMPTY, ...pick(raw) };
  } catch {
    return { ...EMPTY };
  }
}

function pick(raw: Record<string, unknown>): Partial<ModelSettings> {
  const out: Partial<ModelSettings> = {};
  for (const k of Object.keys(EMPTY) as (keyof ModelSettings)[]) {
    if (typeof raw[k] === "string") out[k] = raw[k] as string;
  }
  return out;
}

/** Returns an error message per invalid field; empty object means valid. */
export function validateSettings(s: ModelSettings): Partial<Record<keyof ModelSettings, string>> {
  const errors: Partial<Record<keyof ModelSettings, string>> = {};
  if (s.baseUrl) {
    try {
      const u = new URL(s.baseUrl);
      if (u.protocol !== "http:" && u.protocol !== "https:") errors.baseUrl = "Нужен http:// или https://";
      else if (!isLoopback(s.baseUrl)) errors.baseUrl = LOOPBACK_ERROR;
    } catch {
      errors.baseUrl = "Это не URL";
    }
  }
  if (s.paramsB && !/^\d{1,3}(\.\d{1,3})?$/.test(s.paramsB)) errors.paramsB = "Число, например 8.95";
  if (s.extraBody) {
    try {
      const v = JSON.parse(s.extraBody);
      if (typeof v !== "object" || v === null || Array.isArray(v)) errors.extraBody = "Нужен JSON-объект";
    } catch {
      errors.extraBody = "Невалидный JSON";
    }
  }
  if (s.model && !/^[\w./:-]{1,128}$/.test(s.model)) errors.model = "Только буквы, цифры и ./:-_";
  return errors;
}

export async function writeSettings(s: ModelSettings): Promise<void> {
  await fs.mkdir(DATA, { recursive: true });
  await fs.writeFile(FILE, JSON.stringify(pick(s as unknown as Record<string, unknown>), null, 1));
}

export const LOOPBACK_ERROR = "Только локальный адрес: 127.0.0.0/8, ::1 или localhost (NFR-2.5)";

/** Loopback = 127.0.0.0/8, ::1, localhost. URL() already canonicalises IPv4 forms like 127.1 or 0x7f.1. */
export function isLoopback(baseUrl: string): boolean {
  try {
    const h = new URL(baseUrl).hostname;
    return h === "localhost" || h === "[::1]" || /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h);
  } catch {
    return false;
  }
}

/** RESEARCH_LLM_* for the polygon. The API key is never stored: it comes from the server env. */
export function modelEnv(s: ModelSettings): Record<string, string> {
  const env: Record<string, string> = {
    RESEARCH_LLM_BASE_URL: s.baseUrl,
    RESEARCH_LLM_MODEL: s.model,
  };
  if (s.paramsB) env.RESEARCH_LLM_PARAMS_B = s.paramsB;
  if (s.extraBody) env.RESEARCH_LLM_EXTRA_BODY = s.extraBody;
  return env;
}
