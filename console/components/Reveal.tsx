"use client";

import { motion, useReducedMotion } from "motion/react";

/** Children fade and rise in sequence; index sets the delay. */
export function Reveal({ children, i = 0, className }: { children: React.ReactNode; i?: number; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: 0.05 * i, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}
