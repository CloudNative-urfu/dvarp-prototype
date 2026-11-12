"use client";

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Bot, CircleCheck, CircleX, LoaderCircle, Swords, User } from "lucide-react";
import type { ConfigMetrics, Episode, RunDetail } from "@/lib/runs";
import { Segment } from "./controls";
import { Badge, Checks, OkMark, fmt, pct, statusOf, type Tone } from "./ui";

const plural = (n: number) => (n % 10 === 1 && n % 100 !== 11 ? "проверка" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "проверки" : "проверок");

type Tab = { id: string; label: string };

export function RunTabs({ run }: { run: RunDetail }) {
  const tabs = useMemo<Tab[]>(() => {
    const t: Tab[] = [];
    if (run.configs.length) t.push({ id: "metrics", label: "Метрики" });
    if (run.hasDialogs) t.push({ id: "dialogs", label: "Диалоги" });
    if (run.live) t.push({ id: "budget", label: "Бюджет модели" });
    if (run.smoke) t.push({ id: "smoke", label: "Сценарии" });
    if (run.demo) t.push({ id: "demo", label: "Проверки" });
    if (run.checks.length) t.push({ id: "checks", label: "Самопроверки" });
    return t;
  }, [run]);
  const [tab, setTab] = useState(tabs[0]?.id ?? "");
  if (!tabs.length) return <p className="muted">В каталоге нет итоговых файлов.</p>;
  return (
    <div>
      {tabs.length === 1 ? (
        <h2 className="section">{tabs[0].label}</h2>
      ) : (
      <div className="tabs" role="tablist" aria-label="Разделы прогона">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            className="tab"
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {tab === t.id && <motion.span layoutId="tab-line" className="tab-line" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
          </button>
        ))}
      </div>
      )}
      <AnimatePresence mode="wait">
        <motion.div
          key={tab}
          role={tabs.length > 1 ? "tabpanel" : undefined}
          id={`panel-${tab}`}
          aria-labelledby={tabs.length > 1 ? `tab-${tab}` : undefined}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
        >
          {tab === "metrics" && <Metrics run={run} />}
          {tab === "dialogs" && <Dialogs name={run.name} configs={run.configs.map((c) => c.name)} />}
          {tab === "budget" && run.live && <Budget run={run} />}
          {tab === "smoke" && run.smoke && <Smoke run={run} />}
          {tab === "demo" && run.demo && <Demo demo={run.demo} />}
          {tab === "checks" && (
            <section className="panel">
              <Checks checks={run.checks} />
            </section>
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

function Demo({ demo }: { demo: NonNullable<RunDetail["demo"]> }) {
  const cols = demo.rows[0]?.values.map(([k]) => k) ?? [];
  return (
    <div className="stack" style={{ gap: "var(--sp-6)" }}>
      {demo.rows.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Прогон</th>
                {cols.map((c) => (
                  <th key={c} scope="col">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {demo.rows.map((r, i) => (
                <motion.tr key={r.label} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.04 * i }}>
                  <td className="mono">{r.label}</td>
                  {r.values.map(([k, v]) => (
                    <td key={k} className="mono">
                      {v}
                    </td>
                  ))}
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {demo.checks.length > 0 && (
        <section className="panel">
          <h2 className="section">Проверки</h2>
          <Checks checks={demo.checks} />
        </section>
      )}
      {demo.transcripts.map((t) => (
        <article key={t.id} className="episode">
          <div className="episode-head">
            <span className="mono" style={{ fontWeight: 500 }}>
              {t.id}
            </span>
            <Badge tone={verdictOf(t.outcome).tone}>Оценщик: {verdictOf(t.outcome).label}</Badge>
          </div>
          <div className="thread">
            {t.messages.map((m, k) => (
              <div key={k} className="bubble" data-from={m.from}>
                <div className="bubble-who">{m.from === "agent" ? "агент" : m.from === "attacker" ? "атакующий" : "клиент"}</div>
                {m.text}
              </div>
            ))}
          </div>
        </article>
      ))}
    </div>
  );
}

function Bar({ value, tone }: { value: number | null; tone?: "bad" }) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  return (
    <span className="bar" data-tone={tone} aria-hidden>
      <motion.span initial={{ scaleX: 0 }} animate={{ scaleX: v }} transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1], delay: 0.1 }} />
    </span>
  );
}

function Metrics({ run }: { run: RunDetail }) {
  const rows: ConfigMetrics[] = run.configs;
  return (
    <div className="stack" style={{ gap: "var(--sp-6)" }}>
      <div className="table-wrap">
        <table className="data">
          <caption className="sr-only">Метрики по конфигурациям</caption>
          <thead>
            <tr>
              <th scope="col">Конфигурация</th>
              <th scope="col">V, доля нарушений</th>
              <th scope="col" className="num">
                ΔV [95% ДИ]
              </th>
              <th scope="col">Окупаемость</th>
              <th scope="col" className="num">
                L*, $
              </th>
              <th scope="col" className="num">
                Q
              </th>
              <th scope="col" className="num">
                Клиенты
              </th>
              <th scope="col" className="num">
                C_ep, $
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c, i) => {
              const st = statusOf(c.status);
              return (
                <motion.tr key={c.name} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.03 * i, duration: 0.3 }}>
                  <th scope="row" className="mono">
                    {c.name}
                    {run.pareto.includes(c.name) && (
                      <span className="badge" data-tone="ok" style={{ marginLeft: "var(--sp-2)" }}>
                        Pareto
                      </span>
                    )}
                  </th>
                  <td>
                    <span className="row" style={{ flexWrap: "nowrap" }}>
                      <Bar value={c.V} tone={c.V ? "bad" : undefined} />
                      <span>{pct(c.V)}</span>
                    </span>
                  </td>
                  <td className="num">
                    {fmt(c.dV)} <span className="muted">{c.dVci ? `[${fmt(c.dVci[0], 2)}; ${fmt(c.dVci[1], 2)}]` : ""}</span>
                  </td>
                  <td>
                    <Badge tone={st.tone}>{st.label}</Badge>
                  </td>
                  <td className="num">{fmt(c.Lstar)}</td>
                  <td className="num">{fmt(c.Q)}</td>
                  <td className="num">{pct(c.legalShare)}</td>
                  <td className="num">{fmt(c.Cep, 2)}</td>
                </motion.tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="grid-2" style={{ alignItems: "start" }}>
        <section className="panel stack" style={{ gap: "var(--sp-3)" }}>
          <h2 className="section" style={{ margin: 0 }}>
            Вывод
          </h2>
          <p style={{ margin: 0 }}>
            Pareto-фронт: <span className="mono">{run.pareto.join(", ") || "н/д"}</span>
          </p>
          <p style={{ margin: 0 }} className="muted">
            Тратят без выигрыша: <span className="mono">{run.inefficient.join(", ") || "нет"}</span>
          </p>
          {rows
            .filter((c) => c.reasons.length)
            .map((c) => (
              <p key={c.name} className="small muted" style={{ margin: 0 }}>
                <span className="mono">{c.name}</span>: {c.reasons.join("; ")}
              </p>
            ))}
        </section>
        <section className="panel small stack" style={{ gap: "var(--sp-2)" }}>
          <h2 className="section" style={{ margin: 0 }}>
            Как читать
          </h2>
          <p style={{ margin: 0 }} className="muted">
            V: доля атак, которые оценщик по базе признал исполненными. ΔV: насколько защита снизила V относительно g0. L*: цена одного нарушения в
            долларах, начиная с которой защита окупается с учётом ручной проверки, отказов и вычислений. Q: нагрузка на операторов. Клиенты: доля
            легальных клиентов, обслуженных до конца.
          </p>
        </section>
      </div>
    </div>
  );
}

const VERDICT: Record<string, { label: string; tone: Tone }> = {
  executed: { label: "нарушение исполнено", tone: "bad" },
  refused: { label: "отказ", tone: "ok" },
  ignored: { label: "ничего не сделано", tone: "neutral" },
  escalated: { label: "к оператору", tone: "warn" },
  completed: { label: "клиент обслужен", tone: "ok" },
  lost: { label: "клиент потерян", tone: "bad" },
};

const verdictOf = (v: string | null) => (v ? (VERDICT[v] ?? { label: v, tone: "neutral" as Tone }) : { label: "нет вердикта", tone: "neutral" as Tone });

function Dialogs({ name, configs }: { name: string; configs: string[] }) {
  const [eps, setEps] = useState<Episode[] | null>(null);
  const [cfg, setCfg] = useState("all");
  const [traffic, setTraffic] = useState("attack");
  useEffect(() => {
    let off = false;
    fetch(`/api/runs/${name}/dialogs`)
      .then((r) => r.json())
      .then((d) => !off && setEps(Array.isArray(d) ? d : []));
    return () => {
      off = true;
    };
  }, [name]);
  if (!eps)
    return (
      <span className="row muted">
        <LoaderCircle size={16} className="spin" aria-hidden /> Читаю журналы
      </span>
    );
  // executed violations first: they are what the run is about
  const shown = eps
    .filter((e) => (cfg === "all" || e.config === cfg) && (traffic === "all" || e.traffic === traffic))
    .sort((a, b) => Number(b.verdict === "executed") - Number(a.verdict === "executed"));
  const cfgOptions = [{ value: "all", label: "Все" }, ...configs.filter((c) => eps.some((e) => e.config === c)).map((c) => ({ value: c, label: c }))];
  return (
    <div className="stack" style={{ gap: "var(--sp-5)" }}>
      <div className="row" style={{ gap: "var(--sp-4)" }}>
        <Segment label="Конфигурация" options={cfgOptions} value={cfg} onChange={setCfg} />
        <Segment
          label="Трафик"
          options={[
            { value: "attack", label: "Атаки" },
            { value: "legal", label: "Клиенты" },
            { value: "all", label: "Весь трафик" },
          ]}
          value={traffic}
          onChange={setTraffic}
        />
        <span className="muted small">{shown.length} эпизодов</span>
      </div>
      {shown.length === 0 && <p className="muted">Под фильтр ничего не попало.</p>}
      {shown.map((e, i) => (
        <motion.article
          key={`${e.config}-${e.id}`}
          className="episode"
          data-verdict={e.verdict ?? undefined}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: Math.min(i, 8) * 0.05, duration: 0.3 }}
        >
          <div className="episode-head">
            <div className="row">
              <span className="mono" style={{ fontWeight: 500 }}>
                {e.config}
              </span>
              <span className="mono muted small">{e.id}</span>
              {e.traffic === "attack" && <Badge>попытка {e.attempt}</Badge>}
              {e.tactics.length > 0 && <span className="stage">{[...new Set(e.tactics)].join(" → ")}</span>}
            </div>
            <Badge tone={verdictOf(e.verdict).tone}>Оценщик: {verdictOf(e.verdict).label}</Badge>
          </div>
          <div className="thread">
            {e.messages.map((m, k) => (
              <div key={k} className="bubble" data-from={m.from}>
                <div className="bubble-who">
                  {m.from === "agent" ? <Bot size={13} aria-hidden /> : m.from === "attacker" ? <Swords size={13} aria-hidden /> : <User size={13} aria-hidden />}
                  {m.from === "agent" ? "агент" : m.from === "attacker" ? "атакующий" : "клиент"}
                  {m.closed && <span className="muted">· сессия закрыта</span>}
                </div>
                {m.text}
              </div>
            ))}
          </div>
          {e.traffic === "attack" && e.seen && (
            <p className="row small muted" style={{ margin: "var(--sp-4) 0 0" }}>
              {e.believedSuccess ? <CircleCheck size={14} aria-hidden /> : <CircleX size={14} aria-hidden />}
              Атакующий понял ответ как «{e.seen}», {e.believedSuccess ? "поверил в успех и остановился" : "успеха не увидел"}
              {e.notes.filter((n) => n.startsWith("repeated")).length > 0 && (
                <span className="badge" data-tone="warn">
                  повторы: {e.notes.filter((n) => n.startsWith("repeated")).length}
                </span>
              )}
            </p>
          )}
        </motion.article>
      ))}
    </div>
  );
}

function Budget({ run }: { run: RunDetail }) {
  const l = run.live!;
  const used = l.maxRequests ? l.requests / l.maxRequests : 0;
  return (
    <div className="grid-2" style={{ alignItems: "start" }}>
      <section className="panel stack">
        <span className="muted small">Запросы к модели</span>
        <span className="hero-figure">
          {l.requests}
          <span className="muted" style={{ fontSize: "var(--fs-xl)" }}>
            {" "}
            из {l.maxRequests}
          </span>
        </span>
        <span className="bar" style={{ width: "100%", height: "var(--sp-2)" }} aria-hidden>
          <motion.span initial={{ scaleX: 0 }} animate={{ scaleX: used }} transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }} />
        </span>
        {l.limitHit && <Badge tone="warn">лимит достигнут</Badge>}
      </section>
      <section className="panel">
        <dl className="kv" style={{ gridTemplateColumns: "repeat(2, minmax(0,1fr))" }}>
          <div>
            <dt>Модель</dt>
            <dd className="mono small">{l.model || "н/д"}</dd>
          </div>
          <div>
            <dt>Размер</dt>
            <dd>{l.paramsB ? `${l.paramsB}B` : "н/д"}</dd>
          </div>
          <div>
            <dt>Токены, вход</dt>
            <dd>{l.tokensIn.toLocaleString("ru-RU")}</dd>
          </div>
          <div>
            <dt>Токены, выход</dt>
            <dd>{l.tokensOut.toLocaleString("ru-RU")}</dd>
          </div>
          <div>
            <dt>Время</dt>
            <dd>{Math.round(l.elapsed)} с</dd>
          </div>
          <div>
            <dt>Ошибки</dt>
            <dd>{l.failed}</dd>
          </div>
        </dl>
      </section>
    </div>
  );
}

function Smoke({ run }: { run: RunDetail }) {
  const [open, setOpen] = useState<string | null>(null);
  const sc = run.smoke!;
  return (
    <div className="stack" style={{ gap: "var(--sp-4)" }}>
      <p className="lede">
        {sc.filter((s) => s.ok).length} из {sc.length} сценариев прошли. Нажмите на сценарий, чтобы увидеть его проверки.
      </p>
      <ul className="list">
        {sc.map((s) => (
          <li key={s.scenario}>
            <button
              type="button"
              className="list-row"
              style={{ width: "100%", border: 0, background: "transparent", textAlign: "left", cursor: "pointer" }}
              aria-expanded={open === s.scenario}
              onClick={() => setOpen(open === s.scenario ? null : s.scenario)}
            >
              <span className="title mono">{s.scenario}</span>
              <span className="row small muted" style={{ flexWrap: "nowrap" }}>
                {s.checks.length ? `${s.checks.length} ${plural(s.checks.length)}` : "без проверок"}
                {s.ok ? <OkMark label="пройден" /> : <Badge tone="bad">сбой</Badge>}
              </span>
            </button>
            <AnimatePresence initial={false}>
              {open === s.scenario && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} style={{ overflow: "hidden" }}>
                  <div style={{ padding: "0 var(--sp-4) var(--sp-4)" }}>
                    {s.error && <p className="field-error">{s.error}</p>}
                    <Checks checks={s.checks} />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </li>
        ))}
      </ul>
    </div>
  );
}
