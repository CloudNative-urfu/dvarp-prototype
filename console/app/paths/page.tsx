import { listConfigNames } from "@/lib/configs";
import { listRuns } from "@/lib/runs";
import { readSettings } from "@/lib/settings";
import { Launcher } from "@/components/Launcher";
import { Reveal } from "@/components/Reveal";

export const dynamic = "force-dynamic";

export default async function Paths({ searchParams }: { searchParams: Promise<{ action?: string }> }) {
  const [{ action }, configs, runs, s] = await Promise.all([searchParams, listConfigNames(), listRuns(), readSettings()]);
  const finished = runs.filter((r) => r.kind === "offline" || r.kind === "live").map((r) => r.name);
  return (
    <div className="stack-lg">
      <Reveal>
        <header className="page-head">
          <span className="eyebrow">Пути</span>
          <h1 className="display">Запуск без терминала</h1>
          <p className="lede">
            Семь путей полигона. Выберите путь, задайте параметры и запустите: консоль вызовет тот же CLI, что и в терминале, и покажет вывод в реальном
            времени.
          </p>
        </header>
      </Reveal>
      <Reveal i={1}>
        <Launcher configs={configs} runs={finished} initial={action} modelReady={Boolean(s.baseUrl && s.model)} />
      </Reveal>
    </div>
  );
}
