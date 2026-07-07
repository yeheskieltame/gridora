"use client";

// Fixed glass navbar. Every control works: anchors scroll, external links open,
// the hamburger drives the mobile drawer.
import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";

const LINKS = [
  { label: "the tape", href: "#tape" },
  { label: "proof", href: "#proof" },
  { label: "how it works", href: "#how" },
];

export function Clover({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <path
        fill="currentColor"
        d="M16 2c3.3 0 6 2.7 6 6 0 .7-.1 1.4-.3 2 .6-.2 1.3-.3 2.3-.3 3.3 0 6 2.7 6 6s-2.7 6-6 6c-.7 0-1.4-.1-2-.3.2.6.3 1.3.3 2 0 3.3-2.7 6-6 6s-6-2.7-6-6c0-.7.1-1.4.3-2-.6.2-1.3.3-2.3.3-3.3 0-6-2.7-6-6s2.7-6 6-6c.7 0 1.4.1 2 .3-.2-.6-.3-1.3-.3-2 0-3.3 2.7-6 6-6z"
      />
    </svg>
  );
}

export function FlexNav() {
  const [open, setOpen] = useState(false);

  return (
    <header className="fixed top-0 left-0 w-full z-50 py-5 md:py-8 bg-gradient-to-b from-[#f1f1f1]/80 to-transparent backdrop-blur-[2px]">
      <div className="grid grid-cols-12 items-center gap-x-4 max-w-7xl mx-auto px-6 md:px-16 lg:px-20">
        <a href="#top" className="col-span-6 md:col-span-3 flex items-center gap-2.5 text-fg">
          <Clover className="w-7 h-7" />
          <span className="font-display text-xl font-semibold tracking-tight">gridora</span>
        </a>

        <nav className="hidden md:flex col-span-6 items-center justify-center gap-8">
          {LINKS.map((l) => (
            <a key={l.label} href={l.href}
               className="text-[13px] lowercase text-fg/70 hover:text-fg transition-colors">
              {l.label}
            </a>
          ))}
        </nav>

        <div className="col-span-6 md:col-span-3 flex items-center justify-end gap-4">
          <a href="https://www.8004scan.io/agents" target="_blank" rel="noreferrer"
             className="hidden sm:block text-[13px] lowercase text-fg/70 hover:text-fg transition-colors">
            erc-8004 ↗
          </a>
          <a href="#tape"
             className="hidden sm:inline-flex items-center gap-1.5 bg-fg text-white text-[13px] lowercase rounded-full px-5 py-2.5 hover:bg-black transition-colors">
            watch the tape <span aria-hidden>↓</span>
          </a>
          <button aria-label="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}
                  className="md:hidden relative w-9 h-9 flex flex-col items-center justify-center gap-[5px]">
            <motion.span animate={open ? { rotate: 45, y: 3.5 } : { rotate: 0, y: 0 }}
                         className="block w-5 h-[2px] bg-fg rounded-full" />
            <motion.span animate={open ? { rotate: -45, y: -3.5 } : { rotate: 0, y: 0 }}
                         className="block w-5 h-[2px] bg-fg rounded-full" />
          </button>
        </div>
      </div>

      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -12 }} transition={{ duration: 0.25, ease: "easeOut" }}
                      className="md:hidden mx-4 mt-4 rounded-2xl bg-white/90 backdrop-blur-md border border-line shadow-lg p-5 flex flex-col gap-4">
            {[...LINKS, { label: "erc-8004 ↗", href: "https://www.8004scan.io/agents" }].map((l) => (
              <a key={l.label} href={l.href} onClick={() => setOpen(false)}
                 className="text-[15px] lowercase text-fg">
                {l.label}
              </a>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
