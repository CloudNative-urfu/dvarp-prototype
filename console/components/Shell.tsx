"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { Bot, FlaskConical, History, Layers, LayoutDashboard, Menu, Route, Settings, ShieldCheck, X } from "lucide-react";

const NAV = [
  { href: "/", label: "Обзор", icon: LayoutDashboard },
  { href: "/paths", label: "Пути", icon: Route },
  { href: "/jobs", label: "Задачи", icon: History },
  { href: "/results", label: "Результаты", icon: FlaskConical },
  { href: "/configs", label: "Конфигурации", icon: Layers },
  { href: "/actors", label: "Агент и атакующий", icon: Bot },
  { href: "/settings", label: "Модель", icon: Settings },
];

function active(path: string, href: string) {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
}

function Brand() {
  return (
    <Link href="/" className="brand" aria-label="DARP консоль, обзор">
      <span className="brand-mark">
        <ShieldCheck size={17} strokeWidth={2.2} aria-hidden />
      </span>
      <span>
        DARP
        <small>полигон гвардрейлов</small>
      </span>
    </Link>
  );
}

function Nav({ id, onNavigate }: { id: string; onNavigate?: () => void }) {
  const path = usePathname();
  return (
    <nav className="nav" aria-label="Разделы">
      {NAV.map(({ href, label, icon: Icon }) => {
        const on = active(path, href);
        return (
          <Link key={href} href={href} className="nav-item" aria-current={on ? "page" : undefined} onClick={onNavigate}>
            {on && <motion.span layoutId={`pill-${id}`} className="nav-pill" transition={{ type: "spring", stiffness: 520, damping: 40 }} />}
            <Icon size={18} aria-hidden />
            <span>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  return (
    <MotionConfig reducedMotion="user">
    <div className="shell">
      <header className="topbar">
        <Brand />
        <button
          type="button"
          className="icon-btn"
          aria-label={open ? "Закрыть меню" : "Открыть меню"}
          aria-expanded={open}
          aria-controls="mobile-nav"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <X size={18} aria-hidden /> : <Menu size={18} aria-hidden />}
        </button>
      </header>
      <AnimatePresence>
        {open && (
          <>
            <motion.div className="scrim" initial={{ opacity: 0 }} animate={{ opacity: 0.7 }} exit={{ opacity: 0 }} onClick={() => setOpen(false)} />
            <motion.aside
              id="mobile-nav"
              className="sidebar sidebar-mobile"
              initial={{ opacity: 0, y: -12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            >
              <Nav id="m" onNavigate={() => setOpen(false)} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>
      <aside className="sidebar sidebar-desktop">
        <Brand />
        <Nav id="d" />
        <div className="sidebar-foot">
          Локально, 127.0.0.1
          <br />
          CloudNative-urfu/dvarp-prototype
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
    </MotionConfig>
  );
}
