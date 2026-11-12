import Link from "next/link";
import type { JobMeta } from "@/lib/jobs";
import { JobBadge, OkMark, ago, duration } from "./ui";

export function JobRows({ jobs, empty }: { jobs: JobMeta[]; empty: React.ReactNode }) {
  if (!jobs.length) return <>{empty}</>;
  return (
    <ul className="list">
      {jobs.map((j) => (
        <li key={j.id}>
          <Link href={`/jobs/${j.id}`} className="list-row">
            <span style={{ minWidth: 0 }}>
              <span className="title" style={{ display: "block" }}>
                {j.title}
                {j.run ? <span className="mono muted small"> · {j.run}</span> : null}
              </span>
              <span className="meta">
                {ago(j.startedAt)}
                {j.endedAt ? ` · ${duration(j.endedAt - j.startedAt)}` : ""}
                {j.usesModel ? " · живая модель" : ""}
              </span>
            </span>
            {j.status === "ok" ? <OkMark label="готово" /> : <JobBadge status={j.status} />}
          </Link>
        </li>
      ))}
    </ul>
  );
}
