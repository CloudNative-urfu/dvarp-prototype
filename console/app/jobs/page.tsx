import Link from "next/link";
import { listJobs } from "@/lib/jobs";
import { AutoRefresh } from "@/components/AutoRefresh";
import { JobRows } from "@/components/JobRows";
import { Reveal } from "@/components/Reveal";

export const dynamic = "force-dynamic";

export default async function Jobs() {
  const jobs = await listJobs(100);
  return (
    <div className="stack-lg">
      <AutoRefresh active={jobs.some((j) => j.status === "running")} />
      <Reveal>
        <header className="page-head">
          <span className="eyebrow">Задачи</span>
          <h1 className="display">История запусков</h1>
          <p className="lede">Каждый запуск сохраняет команду, полный вывод и код выхода. Задачу можно открыть и после перезапуска консоли.</p>
        </header>
      </Reveal>
      <Reveal i={1}>
        <JobRows
          jobs={jobs}
          empty={
            <div className="empty">
              <strong>Запусков ещё не было</strong>
              <Link href="/paths" className="btn btn-primary">
                К путям
              </Link>
            </div>
          }
        />
      </Reveal>
    </div>
  );
}
