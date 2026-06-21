// Gridora wordmark — the brand mark (coral grid "G", transparent so it reads on both
// themes) next to the lowercase name. Served from /public so it never waits on a font.

export function Wordmark() {
  return (
    <a
      href="/"
      aria-label="Gridora"
      className="group inline-flex items-center gap-2.5 text-fg transition-colors"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/gridora-mark.svg"
        alt=""
        width={22}
        height={22}
        className="transition-transform duration-300 group-hover:scale-110"
      />
      <span className="font-sans text-base font-semibold tracking-tight">gridora</span>
    </a>
  );
}
