// Minimal inline SVG icons (currentColor) so the page needs no icon dependency.
type IconProps = { className?: string; strokeWidth?: number };

function Svg({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden className={className}>
      {children}
    </svg>
  );
}

export function ChevronDown({ className, strokeWidth = 2 }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
    </Svg>
  );
}

export function ChevronRight({ className, strokeWidth = 2 }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M9 6l6 6-6 6" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
    </Svg>
  );
}

export function ArrowUpRight({ className, strokeWidth = 2 }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M7 17 17 7M9 7h8v8" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
    </Svg>
  );
}

export function Copy({ className, strokeWidth = 2 }: IconProps) {
  return (
    <Svg className={className}>
      <rect x="9" y="9" width="11" height="11" rx="2" stroke="currentColor" strokeWidth={strokeWidth} />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" />
    </Svg>
  );
}

export function Check({ className, strokeWidth = 2.5 }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
    </Svg>
  );
}

export function Sun({ className, strokeWidth = 1.8 }: IconProps) {
  return (
    <Svg className={className}>
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth={strokeWidth} />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
            stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" />
    </Svg>
  );
}

export function Moon({ className, strokeWidth = 1.8 }: IconProps) {
  return (
    <Svg className={className}>
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" stroke="currentColor" strokeWidth={strokeWidth} strokeLinejoin="round" />
    </Svg>
  );
}
