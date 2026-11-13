"use client";

import { useCallback, useEffect, useState } from "react";
import { motion } from "motion/react";
import { Cpu, Lock, RefreshCw, ShieldCheck, SquareTerminal } from "lucide-react";
import type { Health } from "@/lib/system";
import type { Tone } from "./ui";

type Row = { icon: React.ElementType; title: string; detail: string; tone: Tone; state: string };

function rows(h: Health): Row[] {
  const m = h.model;
  return [
    { icon: Lock, title: "Заморозка конфигураций", detail: h.freeze.ok ? "хеши совпадают с configs.lock.json" : h.freeze.detail || "проверка не прошла", tone: h.freeze.ok ? "ok" : "bad", state: h.freeze.ok ? "OK" : "сбой" },
    {
      icon: ShieldCheck,
      title: "Песочница атакующего",
      detail: h.sandbox.path ? (/bubblewrap-([\d.]+)/.exec(h.sandbox.path)?.[1] ? `bubblewrap ${/bubblewrap-([\d.]+)/.exec(h.sandbox.path)?.[1]}` : "bwrap в PATH") : "bwrap не найден в PATH, режим A живьём не запустится",
      tone: h.sandbox.ok ? "ok" : "bad",
      state: h.sandbox.ok ? "есть" : "нет",
    },
    {
      icon: Cpu,
      title: "Модель",
      detail: !m.configured ? "адрес и имя не заданы" : !m.loopback ? `${m.baseUrl}: адрес не локальный, запросы заблокированы (NFR-2.5)` : m.reachable ? `${m.name} на ${m.baseUrl}` : `${m.baseUrl} не отвечает${m.error ? `: ${m.error}` : ""}`,
      tone: !m.configured ? "warn" : m.reachable ? "ok" : "bad",
      state: !m.configured ? "не задана" : !m.loopback ? "заблокирована" : m.reachable ? "отвечает" : "недоступна",
    },
    { icon: SquareTerminal, title: "Python", detail: h.python.version, tone: h.python.ok ? "ok" : "bad", state: h.python.ok ? "есть" : "нет" },
  ];
}

export function HealthPanel() {
  const [h, setH] = useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      setH(await (await fetch("/api/health", { cache: "no-store" })).json());
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);
  return (
    <section className="panel" aria-busy={loading}>
      <div className="spread" style={{ marginBottom: "var(--sp-2)" }}>
        <h2 className="section" style={{ margin: 0 }}>
          Состояние стенда
        </h2>
        <button type="button" className="icon-btn" onClick={load} disabled={loading} aria-label="Проверить снова">
          <RefreshCw size={16} className={loading ? "spin" : undefined} aria-hidden />
        </button>
      </div>
      <ul className="status-list">
        {(h ? rows(h) : Array.from({ length: 4 }, () => null)).map((r, i) => (
          <motion.li key={i} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.05 }}>
            {r ? (
              <>
                <span className="status-icon" data-tone={r.tone}>
                  <r.icon size={15} aria-hidden />
                </span>
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: "block", fontWeight: 500 }}>{r.title}</span>
                  <span className="small muted" style={{ overflowWrap: "anywhere" }}>
                    {r.detail}
                  </span>
                </span>
                <span className="badge" data-tone={r.tone}>
                  {r.state}
                </span>
              </>
            ) : (
              <>
                <span className="status-icon" />
                <span className="muted small">проверяю</span>
                <span />
              </>
            )}
          </motion.li>
        ))}
      </ul>
    </section>
  );
}
