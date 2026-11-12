import { CircleCheck, CircleDashed, CircleX, LoaderCircle, Square } from "lucide-react";
import type { JobStatus } from "@/lib/jobs";
import type { Check } from "@/lib/runs";

export type Tone = "ok" | "bad" | "warn" | "neutral";

export function Badge({ tone = "neutral", children, live }: { tone?: Tone; children: React.ReactNode; live?: boolean }) {
  return (
    <span className="badge" data-tone={tone}>
      {(tone !== "neutral" || live) && <span className={live ? "dot dot-live" : "dot"} aria-hidden />}
      {children}
    </span>
  );
}

export const JOB_LABEL: Record<JobStatus, string> = { running: "идёт", ok: "готово", failed: "сбой", stopped: "остановлена" };
export const JOB_TONE: Record<JobStatus, Tone> = { running: "ok", ok: "ok", failed: "bad", stopped: "warn" };

export function JobBadge({ status }: { status: JobStatus }) {
  return (
    <Badge tone={JOB_TONE[status]} live={status === "running"}>
      {JOB_LABEL[status]}
    </Badge>
  );
}

/** Quiet success mark for lists: success is the norm, only problems get a coloured pill. */
export function OkMark({ label }: { label: string }) {
  return (
    <span className="ok-mark" title={label}>
      <CircleCheck size={18} aria-label={label} />
    </span>
  );
}

export function JobIcon({ status, size = 16 }: { status: JobStatus; size?: number }) {
  if (status === "running") return <LoaderCircle size={size} className="spin" aria-hidden />;
  if (status === "ok") return <CircleCheck size={size} aria-hidden />;
  if (status === "failed") return <CircleX size={size} aria-hidden />;
  return <Square size={size} aria-hidden />;
}

export function Checks({ checks }: { checks: Check[] }) {
  if (!checks.length) return <p className="muted small">Самопроверок нет.</p>;
  return (
    <ul className="checks">
      {checks.map((c, i) => (
        <li key={i}>
          <span data-tone={c.pass ? "ok" : "bad"}>
            {c.pass ? <CircleCheck size={16} aria-label="пройдена" /> : <CircleX size={16} aria-label="не пройдена" />}
          </span>
          <span>{c.name}</span>
        </li>
      ))}
    </ul>
  );
}

export function Pending({ label }: { label: string }) {
  return (
    <span className="row muted small">
      <CircleDashed size={16} className="spin" aria-hidden />
      {label}
    </span>
  );
}

export const fmt = (v: number | null | undefined, digits = 3) => {
  if (v === null || v === undefined) return "н/д";
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  return Number(v.toPrecision(digits)).toString();
};

export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "н/д" : `${Math.round(v * 100)}%`);

export function ago(ms: number) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s} с назад`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} мин назад`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} ч назад`;
  return new Date(ms).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

export const duration = (ms: number) => {
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`;
};

export const STATUS_TEXT: Record<string, { label: string; tone: Tone }> = {
  reference: { label: "эталон", tone: "neutral" },
  threshold: { label: "окупается от L*", tone: "ok" },
  pays_at_any_price: { label: "окупается всегда", tone: "ok" },
  never_pays: { label: "не окупается", tone: "bad" },
  unresolved: { label: "не решено", tone: "warn" },
};

export const statusOf = (s: string) => STATUS_TEXT[s] ?? { label: s || "н/д", tone: "neutral" as Tone };
