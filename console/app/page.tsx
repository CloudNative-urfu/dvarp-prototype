import Link from "next/link";
import { ArrowRight, Play } from "lucide-react";
import { ACTIONS } from "@/lib/actions";
import { listJobs } from "@/lib/jobs";
import { getRun, listRuns } from "@/lib/runs";
import { readLock } from "@/lib/configs";
import { HealthPanel } from "@/components/HealthPanel";
import { JobRows } from "@/components/JobRows";
import { Reveal } from "@/components/Reveal";
import { AutoRefresh } from "@/components/AutoRefresh";
import { ago, fmt, pct } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function Overview() {
  const [runs, jobs, lock] = await Promise.all([listRuns(), listJobs(6), readLock()]);
  const latestItem = runs.find((r) => r.kind === "offline" || r.kind === "live");
  const latest = latestItem ? await getRun(latestItem.name) : null;
  const g0 = latest?.configs.find((c) => c.name === "g0");
  const guarded = latest?.configs.filter((c) => c.name !== "g0") ?? [];
  const best = [...guarded].sort((a, b) => (b.dV ?? -1) - (a.dV ?? -1))[0];
  const passed = latest?.checks.filter((c) => c.pass).length ?? 0;

  return (
    <div className="stack-lg">
      <AutoRefresh active={jobs.some((j) => j.status === "running")} ms={4000} />
      <Reveal>
        <header className="page-head">
          <span className="eyebrow">Полигон оценки гвардрейлов агента возвратов</span>
          <h1 className="display">Окупается ли защита агента</h1>
          <p className="lede">
            Агент возвратов интернет-магазина, атакующий и легальные клиенты в одном канале. Оценщик смотрит в базу, а не в текст, и считает, сколько
            стоит каждая защита против нарушений, которые она предотвращает.
          </p>
        </header>
      </Reveal>

      {latest ? (
        <Reveal i={1}>
          <section className="hero" aria-labelledby="hero-title">
            <div className="stack" style={{ gap: "var(--sp-6)" }}>
              <div className="spread">
                <span className="eyebrow">
                  Последний прогон · {latest.kind === "live" ? "живая модель" : "офлайн"} · seed {latest.seeds?.join(", ") ?? "н/д"}
                </span>
                <Link href={`/results/${latest.name}`} className="link mono">
                  {latest.name} <ArrowRight size={16} aria-hidden />
                </Link>
              </div>
              <div>
                <p className="muted small" style={{ margin: 0 }}>
                  Pareto-фронт
                </p>
                <h2 id="hero-title" className="hero-figure mono" style={{ margin: "var(--sp-2) 0 0" }}>
                  {latest.pareto.length ? latest.pareto.join(", ") : "н/д"}
                </h2>
              </div>
              <dl className="kv hero-stats">
                <div>
                  <dt>Нарушения без защиты</dt>
                  <dd>{pct(g0?.V)}</dd>
                </div>
                <div>
                  <dt>Лучший ΔV</dt>
                  <dd>
                    {best ? (
                      <>
                        {fmt(best.dV)} <span className="mono muted small">{best.name}</span>
                      </>
                    ) : (
                      "н/д"
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Самопроверки</dt>
                  <dd>
                    {passed} из {latest.checks.length}
                  </dd>
                </div>
                <div>
                  <dt>Обновлён</dt>
                  <dd>{ago(latest.mtime)}</dd>
                </div>
              </dl>
            </div>
          </section>
        </Reveal>
      ) : (
        <Reveal i={1}>
          <div className="empty">
            <strong>Прогонов ещё нет</strong>
            <span>Запустите офлайн-прогон: он не требует модели и занимает несколько минут.</span>
            <Link href="/paths?action=offline" className="btn btn-primary">
              <Play size={16} aria-hidden /> Офлайн-прогон
            </Link>
          </div>
        </Reveal>
      )}

      <div className="grid-2" style={{ alignItems: "start" }}>
        <Reveal i={2}>
          <section className="panel">
            <div className="spread" style={{ marginBottom: "var(--sp-2)" }}>
              <h2 className="section" style={{ margin: 0 }}>
                Пользовательские пути
              </h2>
              <Link href="/paths" className="link">
                Все пути <ArrowRight size={16} aria-hidden />
              </Link>
            </div>
            <ol className="paths paths-bleed">
              {ACTIONS.map((a, i) => (
                <li key={a.id}>
                  <Link href={`/paths?action=${a.id}`} className="path-item">
                    <span className="path-index">{i + 1}</span>
                    <span>
                      <span className="path-title">{a.title}</span>
                      <br />
                      <span className="path-sub">{a.summary}</span>
                    </span>
                    <ArrowRight size={16} className="muted" aria-hidden />
                  </Link>
                </li>
              ))}
            </ol>
          </section>
        </Reveal>
        <div className="stack">
          <Reveal i={3}>
            <HealthPanel />
          </Reveal>
          {lock && (
            <Reveal i={4}>
              <section className="panel small">
                <div className="spread">
                  <span className="muted">Блокировка конфигураций</span>
                  <span className="mono">{lock.fingerprint.slice(0, 12)}</span>
                </div>
                <p style={{ margin: "var(--sp-2) 0 0" }}>
                  {lock.frozenCount} конфигураций и {lock.codeModules.length} модулей защитного кода заморожены{" "}
                  {new Date(lock.lockedAt).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}
                  {lock.preregistered ? ", предрегистрация" : ""}.
                </p>
              </section>
            </Reveal>
          )}
        </div>
      </div>

      <Reveal i={5}>
        <section>
          <div className="spread" style={{ marginBottom: "var(--sp-4)" }}>
            <h2 className="section" style={{ margin: 0 }}>
              Последние задачи
            </h2>
            <Link href="/jobs" className="link">
              История <ArrowRight size={16} aria-hidden />
            </Link>
          </div>
          <JobRows jobs={jobs} empty={<p className="muted">Задач ещё не было. Запустите любой путь.</p>} />
        </section>
      </Reveal>
    </div>
  );
}
