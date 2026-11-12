"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Clock, Cpu, LoaderCircle, Play, TriangleAlert } from "lucide-react";
import { ACTIONS, type ActionDef, type ParamDef } from "@/lib/actions";
import { ConfigChips, Listbox, Segment } from "./controls";

function stamp() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

function defaults(a: ActionDef, runs: string[]): Record<string, string> {
  const v: Record<string, string> = {};
  for (const p of a.params) {
    if (p.kind === "name") v[p.key] = `${p.default ?? a.id}-${stamp()}`;
    else if (p.kind === "run") v[p.key] = runs[0] ?? "";
    else v[p.key] = p.default ?? "";
  }
  return v;
}

export function Launcher({ configs, runs, initial, modelReady }: { configs: string[]; runs: string[]; initial?: string; modelReady: boolean }) {
  const router = useRouter();
  const [id, setId] = useState(ACTIONS.find((a) => a.id === initial)?.id ?? ACTIONS[0].id);
  const action = ACTIONS.find((a) => a.id === id)!;
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; field?: string } | null>(null);
  const formBox = useRef<HTMLDivElement>(null);
  const pick = (next: ActionDef["id"]) => {
    setId(next);
    // on one-column layouts the form sits below the list: bring it into view
    if (window.matchMedia("(max-width: 1099px)").matches) {
      requestAnimationFrame(() => formBox.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    }
  };

  useEffect(() => {
    // timestamped default names are made on the client so server and client markup agree
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setValues(defaults(action, runs));
    setError(null);
  }, [action, runs]);

  const needsModel = action.usesModel === "always" || (action.usesModel === "optional" && values.model === "live");
  const blocked = needsModel && !modelReady;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: id, params: values }) });
      const d = await r.json();
      if (!r.ok) {
        setError({ message: d.error ?? "Не удалось запустить", field: d.field });
        setBusy(false);
        return;
      }
      router.push(`/jobs/${d.id}`);
    } catch (err) {
      setError({ message: err instanceof Error ? err.message : "Сеть недоступна" });
      setBusy(false);
    }
  };

  const runOptions = useMemo(() => runs.map((r) => ({ value: r, label: r })), [runs]);

  const field = (p: ParamDef) => {
    const v = values[p.key] ?? "";
    const set = (nv: string) => setValues((s) => ({ ...s, [p.key]: nv }));
    const bad = error?.field === p.key;
    const labelId = `lbl-${p.key}`;
    let control: React.ReactNode;
    if (p.kind === "segment") control = <Segment label={p.label} options={p.options ?? []} value={v} onChange={set} />;
    else if (p.kind === "configs") control = <ConfigChips all={configs} value={v} onChange={set} label={p.label} />;
    else if (p.kind === "run") control = <Listbox label={p.label} labelledBy={labelId} options={runOptions} value={v} onChange={set} invalid={bad} placeholder="Нет готовых прогонов" />;
    else
      control = (
        <input
          id={`f-${p.key}`}
          className="input mono"
          value={v}
          inputMode={p.kind === "int" || p.kind === "seeds" ? "numeric" : undefined}
          aria-invalid={bad || undefined}
          aria-describedby={p.hint ? `h-${p.key}` : undefined}
          onChange={(e) => set(e.target.value)}
          spellCheck={false}
          autoComplete="off"
        />
      );
    return (
      <div className="field" key={p.key}>
        {p.kind === "segment" || p.kind === "configs" || p.kind === "run" ? (
          <span className="field-label" id={labelId}>
            {p.label}
          </span>
        ) : (
          <label className="field-label" htmlFor={`f-${p.key}`}>
            {p.label}
          </label>
        )}
        {control}
        {p.hint && (
          <span className="field-hint" id={`h-${p.key}`}>
            {p.hint}
          </span>
        )}
        {bad && <span className="field-error">{error?.message}</span>}
      </div>
    );
  };

  return (
    <div className="launcher">
      <ol className="paths" aria-label="Пользовательские пути">
        {ACTIONS.map((a, i) => (
          <li key={a.id}>
            <button type="button" className="path-item" aria-pressed={a.id === id} onClick={() => pick(a.id)}>
              <span className="path-index">{i + 1}</span>
              <span>
                <span className="path-title">{a.title}</span>
                <br />
                <span className="path-sub">{a.summary}</span>
              </span>
              {a.usesModel === "always" && <Cpu size={16} className="muted" aria-label="нужна модель" />}
            </button>
          </li>
        ))}
      </ol>
      <div ref={formBox} style={{ scrollMarginTop: "var(--size-72)" }}>
      <AnimatePresence mode="wait">
        <motion.form
          key={id}
          className="panel stack"
          onSubmit={submit}
          initial={{ opacity: 0, x: 12 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -12 }}
          transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
          style={{ gap: "var(--sp-5)" }}
        >
          <div className="stack" style={{ gap: "var(--sp-2)" }}>
            <h2 className="section" style={{ margin: 0, fontSize: "var(--fs-xl)" }}>
              {action.title}
            </h2>
            <p className="lede small">{action.detail}</p>
            <span className="row muted small">
              <Clock size={14} aria-hidden /> {action.duration}
            </span>
          </div>
          {action.params.map(field)}
          {blocked && (
            <div className="notice" role="status">
              <TriangleAlert size={16} aria-hidden />
              <span>
                Нужна модель: укажите адрес и имя в разделе <Link href="/settings" style={{ textDecoration: "underline" }}>Модель</Link>.
              </span>
            </div>
          )}
          {error && !error.field && (
            <div className="notice" data-tone="bad" role="alert">
              <TriangleAlert size={16} aria-hidden />
              <span>{error.message}</span>
            </div>
          )}
          <div className="row">
            <button type="submit" className="btn btn-primary" disabled={busy || blocked}>
              {busy ? <LoaderCircle size={16} className="spin" aria-hidden /> : <Play size={16} aria-hidden />}
              {busy ? "Запускаю" : "Запустить"}
            </button>
            <span className="muted small">Команда собирается на сервере из этого описания.</span>
          </div>
        </motion.form>
      </AnimatePresence>
      </div>
    </div>
  );
}
