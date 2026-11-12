import Link from "next/link";
import { listRuns, type RunKind } from "@/lib/runs";
import { Reveal } from "@/components/Reveal";
import { Badge, OkMark, ago } from "@/components/ui";

export const dynamic = "force-dynamic";

const KIND: Record<RunKind, string> = {
  offline: "офлайн-прогон",
  live: "живой прогон",
  shop: "контур магазина",
  agent: "демо агента",
  attackers: "демо атакующего",
  empty: "без итогов",
};

export default async function Results() {
  const runs = (await listRuns()).filter((r) => r.kind !== "empty");
  return (
    <div className="stack-lg">
      <Reveal>
        <header className="page-head">
          <span className="eyebrow">Результаты</span>
          <h1 className="display">Что показали прогоны</h1>
          <p className="lede">Каталоги runs/ с итогами: метрики по конфигурациям, самопроверки, диалоги атакующего с агентом и вердикты оценщика по базе.</p>
        </header>
      </Reveal>
      <Reveal i={1}>
        {runs.length ? (
          <ul className="list">
            {runs.map((r) => (
              <li key={r.name}>
                <Link href={`/results/${r.name}`} className="list-row">
                  <span style={{ minWidth: 0 }}>
                    <span className="title mono" style={{ display: "block" }}>
                      {r.name}
                    </span>
                    <span className="meta">
                      {KIND[r.kind]} · {r.label} · {ago(r.mtime)}
                    </span>
                  </span>
                  {r.ok === null ? <Badge>нет проверок</Badge> : r.ok ? <OkMark label="проверки пройдены" /> : <Badge tone="bad">сбой</Badge>}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <div className="empty">
            <strong>Результатов нет</strong>
            <Link href="/paths" className="btn btn-primary">
              Запустить путь
            </Link>
          </div>
        )}
      </Reveal>
    </div>
  );
}
