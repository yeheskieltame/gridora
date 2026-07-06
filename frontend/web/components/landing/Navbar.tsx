"use client";

// Landing navbar — glassmorphic, fixed, 12-col grid. Theme-independent (always light).
import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";

const LINKS = [
  { label: "how it works", href: "/#how-it-works" },
  { label: "verifier", href: "/verifier" },
  { label: "console", href: "/console" },
  { label: "github", href: "https://github.com/yeheskieltame/gridora" },
];

function Clover({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <path
        fill="#1a1a1a"
        d="M16 2c3.3 0 6 2.7 6 6 0 .7-.1 1.4-.3 2 .6-.2 1.3-.3 2.3-.3 3.3 0 6 2.7 6 6s-2.7 6-6 6c-.7 0-1.4-.1-2-.3.2.6.3 1.3.3 2 0 3.3-2.7 6-6 6s-6-2.7-6-6c0-.7.1-1.4.3-2-.6.2-1.3.3-2.3.3-3.3 0-6-2.7-6-6s2.7-6 6-6c.7 0 1.4.1 2 .3-.2-.6-.3-1.3-.3-2 0-3.3 2.7-6 6-6z"
      />
    </svg>
  );
}

export function LandingNavbar() {
  const [open, setOpen] = useState(false);

  return (
    <header className="fixed top-0 left-0 w-full z-50 py-6 md:py-10 bg-gradient-to-b from-[#f1f1f1]/80 to-transparent backdrop-blur-[2px]">
      <div className="grid grid-cols-12 items-center gap-x-4 max-w-7xl mx-auto px-8 md:px-16 lg:px-20">
        {/* left: brand */}
        <a href="/" className="col-span-6 md:col-span-3 flex items-center gap-2.5">
          <Clover className="w-7 h-7" />
          <span className="font-outfit text-xl font-semibold tracking-tight text-[#1a1a1a]">
            gridora
          </span>
        </a>

        {/* center: desktop links */}
        <nav className="hidden md:flex col-span-6 items-center justify-center gap-8">
          {LINKS.map((l) => (
            <a
              key={l.label}
              href={l.href}
              className="text-[13px] lowercase text-[#1a1a1a]/70 hover:text-[#1a1a1a] transition-colors"
            >
              {l.label}
            </a>
          ))}
        </nav>

        {/* right: docs + CTA + hamburger */}
        <div className="col-span-6 md:col-span-3 flex items-center justify-end gap-4">
          <a
            href="https://github.com/yeheskieltame/gridora#quickstart"
            className="hidden sm:block text-[13px] lowercase text-[#1a1a1a]/70 hover:text-[#1a1a1a] transition-colors"
          >
            docs
          </a>
          <a
            href="/#get-started"
            className="hidden sm:inline-flex items-center gap-1.5 bg-[#1a1a1a] text-white text-[13px] lowercase rounded-full px-5 py-2.5 hover:bg-black transition-colors"
          >
            get started <span aria-hidden>→</span>
          </a>
          <button
            aria-label="menu"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="md:hidden relative w-9 h-9 flex flex-col items-center justify-center gap-[5px]"
          >
            <motion.span
              animate={open ? { rotate: 45, y: 3.5 } : { rotate: 0, y: 0 }}
              className="block w-5 h-[2px] bg-[#1a1a1a] rounded-full"
            />
            <motion.span
              animate={open ? { rotate: -45, y: -3.5 } : { rotate: 0, y: 0 }}
              className="block w-5 h-[2px] bg-[#1a1a1a] rounded-full"
            />
          </button>
        </div>
      </div>

      {/* mobile drawer */}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
            className="md:hidden mx-4 mt-4 rounded-2xl bg-white/85 backdrop-blur-md border border-black/[0.06] shadow-lg p-5 flex flex-col gap-4"
          >
            {LINKS.map((l) => (
              <a
                key={l.label}
                href={l.href}
                onClick={() => setOpen(false)}
                className="text-[15px] lowercase text-[#1a1a1a]"
              >
                {l.label}
              </a>
            ))}
            <a
              href="/#get-started"
              onClick={() => setOpen(false)}
              className="inline-flex items-center justify-center gap-1.5 bg-[#1a1a1a] text-white text-[14px] lowercase rounded-full px-5 py-3"
            >
              get started <span aria-hidden>→</span>
            </a>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
