import { attach, getJob, readLog, type JobEvent } from "@/lib/jobs";

export const dynamic = "force-dynamic";

// Server-sent events: the output so far, then live chunks, then one "end" event with the final meta.
export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const id = (await ctx.params).id;
  const meta = await getJob(id);
  if (!meta) return new Response("not found", { status: 404 });
  const enc = new TextEncoder();
  const send = (c: ReadableStreamDefaultController, event: string, data: unknown) =>
    c.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
  let detach: (() => void) | undefined;
  const stream = new ReadableStream({
    async start(c) {
      const onEvent = (e: JobEvent) => {
        try {
          if (e.type === "log") send(c, "log", e.text);
          else {
            send(c, "end", e.meta);
            detach?.();
            c.close();
          }
        } catch {
          detach?.();
        }
      };
      const a = attach(id, onEvent);
      if (a) {
        detach = a.detach;
        send(c, "meta", meta);
        if (a.output) send(c, "log", a.output);
        return;
      }
      send(c, "meta", meta);
      const log = await readLog(id);
      if (log) send(c, "log", log);
      send(c, "end", (await getJob(id)) ?? meta);
      c.close();
    },
    cancel() {
      detach?.();
    },
  });
  req.signal.addEventListener("abort", () => detach?.());
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache, no-transform", Connection: "keep-alive" },
  });
}
