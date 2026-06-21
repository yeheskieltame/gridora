// Truncated address/hash + explorer link + copy button. Server component
// (the copy button inside is the only client piece).
import { EXPLORER } from "@/lib/chain";
import { fmtAddress, fmtHash } from "@/lib/format";
import { ArrowUpRight } from "./icons";
import { CopyButton } from "./CopyButton";

export function Address({
  value,
  kind = "address",
  className = "",
  label,
}: {
  value: `0x${string}`;
  kind?: "address" | "hash";
  className?: string;
  label?: string;
}) {
  const href = `${EXPLORER}/address/${value}`;
  const display = kind === "hash" ? fmtHash(value) : fmtAddress(value);

  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`} title={value}>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="link-quiet group inline-flex items-center gap-1 text-fg hover:text-accent"
      >
        <span className="font-mono">{display}</span>
        <ArrowUpRight className="h-3 w-3 text-faint transition-colors group-hover:text-accent" />
      </a>
      <CopyButton value={value} label={label ?? kind} />
    </span>
  );
}
