import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { getRun } from "@/lib/runs";
import { RunTabs } from "@/components/RunTabs";
import { Reveal } from "@/components/Reveal";
import { Badge } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function RunPage({ params }: { params: Promise<{ name: string }> }) {
  const run = await getRun((await params).name);
  if (!run) notFound();
  const claim = run.claimLevels ? [...new Set(Object.values(run.claimLevels))].join(", ") : null;
  return (
    <div className="stack-lg">
      <Reveal>
        <header className="page-head">
          <Link href="/results" className="row muted small">
            <ArrowLeft size={14} aria-hidden /> Результаты
          </Link>
          <h1 className="display mono" style={{ letterSpacing: "-0.03em", overflowWrap: "anywhere" }}>
            {run.name}
          </h1>
          <div className="row">
            {run.ok !== null && <Badge tone={run.ok ? "ok" : "bad"}>{run.ok ? "проверки пройдены" : "сбой"}</Badge>}
            {run.mode && <Badge>{run.kind === "live" ? "живая модель" : "офлайн"}</Badge>}
            {run.seeds && <Badge>seed {run.seeds.join(", ")}</Badge>}
            {claim && <Badge>{claim}</Badge>}
          </div>
        </header>
      </Reveal>
      <Reveal i={1}>
        <RunTabs run={run} />
      </Reveal>
    </div>
  );
}
