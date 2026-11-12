import { NextResponse } from "next/server";
import { readSettings } from "@/lib/settings";
import { probeModel } from "@/lib/system";

export const dynamic = "force-dynamic";

export async function GET() {
  const s = await readSettings();
  return NextResponse.json(await probeModel(s.baseUrl));
}
