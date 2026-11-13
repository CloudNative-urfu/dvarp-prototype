"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CircleCheck, LoaderCircle, Plug, Save, TriangleAlert } from "lucide-react";
import type { ModelSettings } from "@/lib/settings";

type Field = { key: keyof ModelSettings; label: string; hint: string; placeholder: string; area?: boolean };

const FIELDS: Field[] = [
  { key: "baseUrl", label: "Адрес API", hint: "OpenAI-совместимый endpoint; только локальный адрес: 127.0.0.0/8, ::1 или localhost (NFR-2.5)", placeholder: "http://127.0.0.1:8000/v1" },
  { key: "model", label: "Имя модели", hint: "как его отдаёт /models", placeholder: "model-name" },
  { key: "paramsB", label: "Размер, млрд параметров", hint: "для учёта затрат в отчёте", placeholder: "8" },
  { key: "extraBody", label: "Extra body, JSON", hint: 'уходит в RESEARCH_LLM_EXTRA_BODY и добавляется к каждому запросу. Для llama.cpp с thinking-моделью: {"chat_template_kwargs":{"enable_thinking":false}}, иначе модель тратит бюджет на рассуждения и отвечает пустотой', placeholder: '{"chat_template_kwargs":{"enable_thinking":false}}', area: true },
];

type Probe = { reachable: boolean; models: string[]; error?: string };

export function SettingsForm({ initial, keyFromEnv }: { initial: ModelSettings; keyFromEnv: boolean }) {
  const [v, setV] = useState(initial);
  const [errors, setErrors] = useState<Partial<Record<keyof ModelSettings, string>>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [probing, setProbing] = useState(false);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaved(false);
    const r = await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) });
    const d = await r.json();
    setSaving(false);
    if (!r.ok) {
      setErrors(d.errors ?? {});
      return;
    }
    setErrors({});
    setSaved(true);
    setTimeout(() => setSaved(false), 2400);
  };

  const check = async () => {
    setProbing(true);
    setProbe(null);
    try {
      setProbe(await (await fetch("/api/model-check", { cache: "no-store" })).json());
    } finally {
      setProbing(false);
    }
  };

  return (
    <form className="panel stack" onSubmit={save} style={{ gap: "var(--sp-5)", maxWidth: "var(--size-640)" }}>
      {FIELDS.map((f) => (
        <div className="field" key={f.key}>
          <label className="field-label" htmlFor={`s-${f.key}`}>
            {f.label}
          </label>
          {f.area ? (
            <textarea
              id={`s-${f.key}`}
              className="textarea"
              value={v[f.key]}
              placeholder={f.placeholder}
              spellCheck={false}
              aria-invalid={Boolean(errors[f.key]) || undefined}
              onChange={(e) => setV({ ...v, [f.key]: e.target.value })}
            />
          ) : (
            <input
              id={`s-${f.key}`}
              className="input mono"
              value={v[f.key]}
              placeholder={f.placeholder}
              spellCheck={false}
              autoComplete="off"
              aria-invalid={Boolean(errors[f.key]) || undefined}
              onChange={(e) => setV({ ...v, [f.key]: e.target.value })}
            />
          )}
          {errors[f.key] ? <span className="field-error">{errors[f.key]}</span> : <span className="field-hint">{f.hint}</span>}
        </div>
      ))}
      <p className="small muted" style={{ margin: 0 }}>
        Ключ API консоль не хранит. Если он нужен, задайте RESEARCH_LLM_API_KEY в окружении сервера консоли
        {keyFromEnv ? ": сейчас он задан." : ": сейчас его нет."}
      </p>
      <div className="row">
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? <LoaderCircle size={16} className="spin" aria-hidden /> : <Save size={16} aria-hidden />}
          Сохранить
        </button>
        <button type="button" className="btn" onClick={check} disabled={probing}>
          {probing ? <LoaderCircle size={16} className="spin" aria-hidden /> : <Plug size={16} aria-hidden />}
          Проверить связь
        </button>
        <AnimatePresence>
          {saved && (
            <motion.span className="row small" style={{ color: "var(--c-accent)" }} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}>
              <CircleCheck size={16} aria-hidden /> Сохранено
            </motion.span>
          )}
        </AnimatePresence>
      </div>
      <AnimatePresence>
        {probe && (
          <motion.div
            className="notice"
            data-tone={probe.reachable ? "ok" : "bad"}
            role="status"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            {probe.reachable ? <CircleCheck size={16} aria-hidden /> : <TriangleAlert size={16} aria-hidden />}
            <span>
              {probe.reachable
                ? `Модель отвечает. Доступно: ${probe.models.join(", ") || "список пуст"}.`
                : `Нет ответа${probe.error ? `: ${probe.error}` : ""}. Проверьте, что сервер модели запущен и адрес сохранён.`}
            </span>
          </motion.div>
        )}
      </AnimatePresence>
    </form>
  );
}
