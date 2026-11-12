"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, Copy, LoaderCircle, Square } from "lucide-react";
import type { JobMeta } from "@/lib/jobs";
import { Checks, JobBadge, duration } from "./ui";

const ANSI = /\x1b\[[0-9;?]*[A-Za-z]/g;
const MAX_LINES = 4000;

function lineClass(l: string) {
  if (/^\s*(PASS|OK)\b/.test(l)) return "ln-pass";
  if (/^\s*FAIL\b|Traceback|Error:|ошибка/i.test(l)) return "ln-fail";
  if (/direnv:/.test(l)) return "ln-dim";
  return undefined;
}

export function JobView({ initial }: { initial: JobMeta }) {
  const [meta, setMeta] = useState(initial);
  const [text, setText] = useState("");
  const [now, setNow] = useState(() => initial.endedAt ?? initial.startedAt);
  const [stopping, setStopping] = useState(false);
  const [copied, setCopied] = useState(false);
  const pre = useRef<HTMLPreElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const es = new EventSource(`/api/jobs/${initial.id}/stream`);
    es.addEventListener("meta", (e) => setMeta(JSON.parse((e as MessageEvent).data)));
    es.addEventListener("log", (e) => setText((t) => t + JSON.parse((e as MessageEvent).data)));
    es.addEventListener("end", (e) => {
      setMeta(JSON.parse((e as MessageEvent).data));
      es.close();
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [initial.id]);

  useEffect(() => {
    if (meta.status !== "running") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [meta.status]);

  const lines = useMemo(() => {
    const all = text.replace(ANSI, "").replace(/\r/g, "").split("\n");
    return all.length > MAX_LINES ? all.slice(-MAX_LINES) : all;
  }, [text]);

  const checks = useMemo(() => {
    const found = lines
      .map((l) => /^\s*(PASS|FAIL)\s+(.+)$/.exec(l))
      .filter((m): m is RegExpExecArray => m !== null)
      .map((m) => ({ pass: m[1] === "PASS", name: m[2].replace(/\s+/g, " ").trim() }));
    if (found.length || meta.status === "running") return found;
    // paths that print a summary instead of PASS/FAIL lines
    const body = lines.filter((l) => !/direnv:/.test(l)).join("\n").trim();
    if (meta.action === "configs-verify" && /^OK$/m.test(body)) {
      return [{ pass: true, name: "Хеши конфигураций и защитного кода совпадают с configs.lock.json" }];
    }
    const start = body.indexOf("{");
    if (start >= 0) {
      try {
        const d = JSON.parse(body.slice(start)) as { ok?: boolean; problems?: unknown[]; configs?: number; log_files?: number };
        if (typeof d.ok === "boolean") {
          const parts = [d.configs !== undefined && `${d.configs} конфигураций`, d.log_files !== undefined && `${d.log_files} журналов`].filter(Boolean);
          return [
            { pass: d.ok, name: `Итог перепроверки${parts.length ? `: ${parts.join(", ")}` : ""}` },
            ...(d.problems ?? []).map((x) => ({ pass: false, name: String(typeof x === "string" ? x : JSON.stringify(x)) })),
          ];
        }
      } catch {
        /* not a JSON summary */
      }
    }
    return found;
  }, [lines, meta.status, meta.action]);

  useEffect(() => {
    const el = pre.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  const stop = async () => {
    setStopping(true);
    await fetch(`/api/jobs/${meta.id}`, { method: "DELETE" });
  };

  const elapsed = (meta.endedAt ?? now) - meta.startedAt;
  const passed = checks.filter((c) => c.pass).length;

  return (
    <div className="stack" style={{ gap: "var(--sp-6)" }}>
      <div className="spread">
        <div className="row">
          <JobBadge status={meta.status} />
          <span className="muted small">{duration(Math.max(0, elapsed))}</span>
          {meta.usesModel && <span className="badge">живая модель</span>}
        </div>
        <div className="row">
          {meta.status === "running" && (
            <button type="button" className="btn btn-danger" onClick={stop} disabled={stopping}>
              {stopping ? <LoaderCircle size={16} className="spin" aria-hidden /> : <Square size={14} aria-hidden />}
              Остановить
            </button>
          )}
          <AnimatePresence>
            {meta.status !== "running" && meta.run && (
              <motion.span initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }}>
                <Link className="btn btn-primary" href={`/results/${meta.run}`}>
                  Результаты <ArrowRight size={16} aria-hidden />
                </Link>
              </motion.span>
            )}
          </AnimatePresence>
        </div>
      </div>

      <div className="row" style={{ flexWrap: "nowrap", alignItems: "stretch" }}>
        <code className="cmd mono" style={{ flex: 1, minWidth: 0 }}>
          {meta.command}
        </code>
        <button
          type="button"
          className="icon-btn"
          style={{ height: "auto" }}
          aria-label={copied ? "Скопировано" : "Скопировать команду"}
          onClick={() => {
            void navigator.clipboard?.writeText(meta.command);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          <Copy size={16} aria-hidden />
        </button>
      </div>

      <div className="grid-2" style={{ alignItems: "start" }}>
        <div className="term">
          <div className="term-head">
            <span>вывод процесса</span>
            <span>{lines.length} строк</span>
          </div>
          <pre
            ref={pre}
            tabIndex={0}
            aria-label="Вывод процесса"
            aria-live="off"
            onScroll={(e) => {
              const el = e.currentTarget;
              stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
            }}
          >
            {lines.map((l, i) => (
              <div key={i} className={lineClass(l)}>
                {l || " "}
              </div>
            ))}
            {meta.status === "running" && <span className="ln-dim">▍</span>}
          </pre>
        </div>
        <section className="panel" aria-live="polite">
          <div className="spread" style={{ marginBottom: "var(--sp-3)" }}>
            <h2 className="section" style={{ margin: 0 }}>
              Самопроверки
            </h2>
            {checks.length > 0 && (
              <span className={passed === checks.length ? "badge" : "badge"} data-tone={passed === checks.length ? "ok" : "bad"}>
                {passed} из {checks.length}
              </span>
            )}
          </div>
          {checks.length ? (
            <Checks checks={checks} />
          ) : (
            <p className="muted small">
              {meta.status === "running" ? "Появятся по мере вывода PASS и FAIL." : "Процесс не печатал строк PASS или FAIL."}
            </p>
          )}
          {meta.status !== "running" && (
            <p className="small muted" style={{ marginTop: "var(--sp-4)" }}>
              Код выхода: <span className="mono">{String(meta.exitCode ?? "н/д")}</span>
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
