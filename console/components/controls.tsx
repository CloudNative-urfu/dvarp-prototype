"use client";

import { useEffect, useId, useRef, useState } from "react";
import { motion } from "motion/react";
import { Check, ChevronDown } from "lucide-react";

type Option = { value: string; label: string };

/** Radio group drawn as a segmented control; arrow keys move the choice. */
export function Segment({ label, options, value, onChange, disabled }: { label: string; options: Option[]; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const id = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (i: number) => {
    const n = (i + options.length) % options.length;
    onChange(options[n].value);
    refs.current[n]?.focus();
  };
  return (
    <div className="segment" role="radiogroup" aria-label={label}>
      {options.map((o, i) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            disabled={disabled}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowDown") (e.preventDefault(), move(i + 1));
              if (e.key === "ArrowLeft" || e.key === "ArrowUp") (e.preventDefault(), move(i - 1));
            }}
          >
            {on && <motion.span layoutId={`seg-${id}`} className="segment-thumb" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
            <span>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

/** Listbox pattern in place of a native select: button trigger, arrows, Home/End, typeahead, Escape. */
export function Listbox({ label, options, value, onChange, placeholder, invalid, labelledBy }: { label: string; options: Option[]; value: string; onChange: (v: string) => void; placeholder?: string; invalid?: boolean; labelledBy?: string }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const root = useRef<HTMLDivElement>(null);
  const typed = useRef({ s: "", t: 0 });
  const current = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const openAt = (i: number) => {
    setActive(Math.max(0, Math.min(options.length - 1, i)));
    setOpen(true);
  };
  const choose = (i: number) => {
    if (options[i]) onChange(options[i].value);
    setOpen(false);
  };
  const onKey = (e: React.KeyboardEvent) => {
    const idx = Math.max(0, options.findIndex((o) => o.value === value));
    if (!open && ["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      openAt(idx);
      return;
    }
    if (!open) return;
    if (e.key === "ArrowDown") (e.preventDefault(), setActive((a) => Math.min(options.length - 1, a + 1)));
    else if (e.key === "ArrowUp") (e.preventDefault(), setActive((a) => Math.max(0, a - 1)));
    else if (e.key === "Home") (e.preventDefault(), setActive(0));
    else if (e.key === "End") (e.preventDefault(), setActive(options.length - 1));
    else if (e.key === "Enter" || e.key === " ") (e.preventDefault(), choose(active));
    else if (e.key === "Escape" || e.key === "Tab") setOpen(false);
    else if (e.key.length === 1) {
      const now = Date.now();
      typed.current = { s: (now - typed.current.t < 600 ? typed.current.s : "") + e.key.toLowerCase(), t: now };
      const hit = options.findIndex((o) => o.label.toLowerCase().startsWith(typed.current.s));
      if (hit >= 0) setActive(hit);
    }
  };

  return (
    <div className="lb" ref={root}>
      <button
        type="button"
        className="lb-trigger"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={labelledBy ? undefined : label}
        aria-labelledby={labelledBy}
        aria-invalid={invalid || undefined}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        onClick={() => (open ? setOpen(false) : openAt(Math.max(0, options.findIndex((o) => o.value === value))))}
        onKeyDown={onKey}
      >
        <span className={current ? "mono small" : "muted"}>{current?.label ?? placeholder ?? "Выберите"}</span>
        <ChevronDown size={16} aria-hidden />
      </button>
      {open && (
        <motion.ul
          id={listId}
          role="listbox"
          aria-label={label}
          className="lb-list"
          initial={{ opacity: 0, y: -6, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
        >
          {options.length === 0 && <li className="lb-option muted">Пусто</li>}
          {options.map((o, i) => (
            <li
              key={o.value}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={o.value === value}
              data-active={i === active}
              className="lb-option mono"
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(i)}
            >
              {o.label}
              {o.value === value && <Check size={14} aria-hidden />}
            </li>
          ))}
        </motion.ul>
      )}
    </div>
  );
}

/** Multi-choice of configurations as toggle chips. */
export function ConfigChips({ all, value, onChange, label }: { all: string[]; value: string; onChange: (v: string) => void; label: string }) {
  const chosen = new Set(value.split(",").map((s) => s.trim()).filter(Boolean));
  const toggle = (c: string) => {
    const next = new Set(chosen);
    if (next.has(c)) next.delete(c);
    else next.add(c);
    onChange(all.filter((x) => next.has(x)).join(","));
  };
  return (
    <div className="stack" style={{ gap: "var(--sp-2)" }}>
      <div className="chips" role="group" aria-label={label}>
        {all.map((c) => (
          <button key={c} type="button" className="chip" aria-pressed={chosen.has(c)} onClick={() => toggle(c)}>
            {chosen.has(c) && <Check size={12} aria-hidden />}
            {c}
          </button>
        ))}
      </div>
      <div className="row small">
        <button type="button" className="btn btn-ghost" onClick={() => onChange(all.join(","))}>
          Все
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => onChange("")}>
          Сбросить
        </button>
        <span className="muted">выбрано {chosen.size}</span>
      </div>
    </div>
  );
}
