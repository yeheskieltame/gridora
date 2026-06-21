"use client";

// Light/dark toggle. Reads the class set by the no-flash script in layout, flips it on
// <html>, and persists the choice. Shows the icon of the theme you'd switch TO.
import { useEffect, useState } from "react";
import { Moon, Sun } from "./icons";

export function ThemeToggle() {
  const [dark, setDark] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    setMounted(true);
  }, []);

  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("gridora-theme", next ? "dark" : "light");
    } catch {
      /* storage blocked — still toggles for this session */
    }
    setDark(next);
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Light mode" : "Dark mode"}
      className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md border border-line text-muted transition-colors hover:border-accent/50 hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
    >
      {/* avoid hydration mismatch: render a neutral icon until mounted */}
      {!mounted ? <Sun className="h-4 w-4" /> : dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  );
}
