"use client";

// Copy-to-clipboard button — small icon that flips to a check for 1.2s on success.
import { useState } from "react";
import { Check, Copy } from "./icons";

export function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  const handle = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // No-op (e.g. insecure context) — the value is visible for manual copy.
    }
  };

  return (
    <button
      type="button"
      onClick={handle}
      aria-label={label ? `Copy ${label}` : "Copy"}
      className="inline-flex h-5 w-5 cursor-pointer items-center justify-center rounded text-faint transition-colors hover:text-fg focus:text-fg focus:outline-none"
    >
      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
    </button>
  );
}
