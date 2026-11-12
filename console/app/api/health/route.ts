import { NextResponse } from "next/server";
import { health } from "@/lib/system";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(await health());
}
