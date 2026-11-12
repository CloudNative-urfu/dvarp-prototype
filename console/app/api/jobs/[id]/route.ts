import { NextResponse } from "next/server";
import { getJob, stopJob } from "@/lib/jobs";

export const dynamic = "force-dynamic";

export async function GET(_: Request, ctx: { params: Promise<{ id: string }> }) {
  const meta = await getJob((await ctx.params).id);
  return meta ? NextResponse.json(meta) : NextResponse.json({ error: "Нет такой задачи" }, { status: 404 });
}

export async function DELETE(_: Request, ctx: { params: Promise<{ id: string }> }) {
  const ok = stopJob((await ctx.params).id);
  return NextResponse.json({ stopped: ok }, { status: ok ? 200 : 409 });
}
