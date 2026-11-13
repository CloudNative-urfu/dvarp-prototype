"use client";

import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Lock, Search } from "lucide-react";
import type { ConfigInfo } from "@/lib/configs";
import { Segment } from "./controls";

const FAMILY: Record<string, string> = { g0: "без защиты", guarded: "гварды", g_proc: "правила процесса" };

export function ConfigGrid({ configs }: { configs: ConfigInfo[] }) {
  const [q, setQ] = useState("");
  const [fam, setFam] = useState("all");
  const [open, setOpen] = useState<string | null>(null);
  const families = useMemo(() => [...new Set(configs.map((c) => c.family))], [configs]);
  const shown = configs.filter(
    (c) =>
      (fam === "all" || c.family === fam) &&
      (!q || `${c.name} ${c.notes} ${c.guards.map((g) => g.id).join(" ")}`.toLowerCase().includes(q.toLowerCase())),
  );
  return (
    <div className="stack" style={{ gap: "var(--sp-5)" }}>
      <div className="row" style={{ gap: "var(--sp-4)" }}>
        <label className="search">
          <span className="sr-only">Поиск по конфигурациям</span>
          <Search size={16} aria-hidden />
          <input className="input" placeholder="Имя, гвард или описание" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <Segment
          label="Семейство"
          options={[{ value: "all", label: "Все" }, ...families.map((f) => ({ value: f, label: FAMILY[f] ?? f }))]}
          value={fam}
          onChange={setFam}
        />
      </div>
      <motion.div layout className="cfg-grid">
        <AnimatePresence>
          {shown.map((c) => {
            const isOpen = open === c.name;
            return (
              <motion.button
                layout
                key={c.name}
                type="button"
                className="cfg"
                aria-expanded={isOpen}
                onClick={() => setOpen(isOpen ? null : c.name)}
                initial={{ opacity: 0, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.98 }}
                transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
              >
                <span className="cfg-head">
                  <span className="cfg-name">{c.name}</span>
                  {!c.frozen && (
                    <span className="badge" data-tone="warn">
                      <Lock size={11} aria-hidden /> не заморожена
                    </span>
                  )}
                </span>
                <span className="small muted" lang="en">
                  {c.notes}
                </span>
                <span className="row" style={{ gap: "6px" }}>
                  {c.guards.length ? (
                    c.guards.map((g) => (
                      <span key={g.id} className="stage">
                        {g.id}
                        {g.stages.length ? ` · ${g.stages.join(", ")}` : ""}
                      </span>
                    ))
                  ) : (
                    <span className="stage">{FAMILY[c.family] ?? c.family}</span>
                  )}
                  {c.controlOf && <span className="stage">контроль {c.controlOf}</span>}
                </span>
                <AnimatePresence initial={false}>
                  {isOpen && (
                    <motion.span
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      style={{ overflow: "hidden", width: "100%" }}
                    >
                      <span className="stack" style={{ gap: "var(--sp-2)", paddingTop: "var(--sp-2)" }}>
                        <span className="field-label">Операционное определение</span>
                        <span className="def-lines">{c.definition.join("\n") || "нет"}</span>
                        <span className="mono muted" style={{ fontSize: "var(--fs-xs)", overflowWrap: "anywhere" }}>
                          fingerprint {c.fingerprint.slice(0, 24)}
                        </span>
                      </span>
                    </motion.span>
                  )}
                </AnimatePresence>
              </motion.button>
            );
          })}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
