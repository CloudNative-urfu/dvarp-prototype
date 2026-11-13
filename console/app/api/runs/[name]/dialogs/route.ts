import { NextResponse } from "next/server";
import { getDialogs } from "@/lib/runs";

export const dynamic = "force-dynamic";

export async function GET(_: Request, ctx: { params: Promise<{ name: string }> }) {
  const d = await getDialogs((await ctx.params).name);
  return d ? NextResponse.json(d) : NextResponse.json({ error: "Нет такого прогона" }, { status: 404 });
}
