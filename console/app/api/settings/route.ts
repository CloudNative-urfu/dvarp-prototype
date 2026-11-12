import { NextResponse } from "next/server";
import { readSettings, validateSettings, writeSettings, type ModelSettings } from "@/lib/settings";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({ ...(await readSettings()), keyFromEnv: Boolean(process.env.RESEARCH_LLM_API_KEY) });
}

export async function PUT(req: Request) {
  let raw: Record<string, unknown>;
  try {
    raw = await req.json();
  } catch {
    return NextResponse.json({ error: "Ожидался JSON" }, { status: 400 });
  }
  const s: ModelSettings = {
    baseUrl: String(raw.baseUrl ?? "").trim(),
    model: String(raw.model ?? "").trim(),
    paramsB: String(raw.paramsB ?? "").trim(),
    extraBody: String(raw.extraBody ?? "").trim(),
  };
  const errors = validateSettings(s);
  if (Object.keys(errors).length) return NextResponse.json({ errors }, { status: 422 });
  await writeSettings(s);
  return NextResponse.json(s);
}
