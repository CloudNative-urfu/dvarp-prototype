import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { getJob } from "@/lib/jobs";
import { JobView } from "@/components/JobView";
import { Reveal } from "@/components/Reveal";

export const dynamic = "force-dynamic";

export default async function Job({ params }: { params: Promise<{ id: string }> }) {
  const meta = await getJob((await params).id);
  if (!meta) notFound();
  return (
    <div className="stack-lg">
      <Reveal>
        <header className="page-head">
          <Link href="/jobs" className="row muted small">
            <ArrowLeft size={14} aria-hidden /> Задачи
          </Link>
          <h1 className="display">{meta.title}</h1>
          {meta.run && <span className="mono muted">runs/{meta.run}</span>}
        </header>
      </Reveal>
      <Reveal i={1}>
        <JobView initial={meta} />
      </Reveal>
    </div>
  );
}
