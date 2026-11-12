import { NextResponse } from "next/server";
import { JobError, listJobs, startJob } from "@/lib/jobs";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(await listJobs());
}

export async function POST(req: Request) {
  let body: { action?: unknown; params?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Ожидался JSON" }, { status: 400 });
  }
  const params: Record<string, string> = {};
  if (body.params && typeof body.params === "object") {
    for (const [k, v] of Object.entries(body.params as Record<string, unknown>)) if (typeof v === "string") params[k] = v;
  }
  try {
    const meta = await startJob(String(body.action ?? ""), params);
    return NextResponse.json(meta, { status: 201 });
  } catch (e) {
    if (e instanceof JobError) return NextResponse.json({ error: e.message, field: e.field }, { status: 422 });
    throw e;
  }
}
