// Landing product-intro sections — static, same fixed light palette as the hero.
// Server-renderable (no client hooks): features, how it works, get started, footer.

const FEATURES = [
  {
    title: "non-custodial",
    body: "Every order is signed locally through the Trust Wallet Agent Kit. Your keys never leave your machine — Gridora can trade for you, but it can never take from you.",
  },
  {
    title: "verifiable",
    body: "An ERC-8004 identity plus an append-only on-chain journal on BNB Smart Chain. Strategy config is committed before trading and attested after — anyone can audit it, no login, no trust.",
  },
  {
    title: "autonomous",
    body: "A dip-turn engine distilled from real trading history: buys confirmed discounts, ratchets profits so a green trade never turns red, and sits in cash when the market says no.",
  },
];

const STEPS = [
  {
    n: "01",
    title: "sense",
    body: "Scans verified BNB Chain markets with live data paid per-call via x402 micropayments — no API keys, no subscriptions.",
  },
  {
    n: "02",
    title: "act",
    body: "Enters only on confirmed dip-turns behind hard guardrails; a trailing ratchet locks break-even the moment a position turns green.",
  },
  {
    n: "03",
    title: "prove",
    body: "Every settled trade is journaled on-chain under the agent's ERC-8004 identity — the public verifier reads it straight from BSC.",
  },
];

export function LandingSections() {
  return (
    <>
      {/* features */}
      <section className="relative z-10 bg-bg-base pb-24">
        <div className="max-w-7xl mx-auto px-8 md:px-16 lg:px-20 grid grid-cols-12 gap-x-4 md:gap-x-8 gap-y-6">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className="col-span-12 md:col-span-4 bg-white rounded-2xl border border-black/[0.05] shadow-sm p-8"
            >
              <div className="w-2.5 h-2.5 rounded-full bg-brand-green mb-5" />
              <h3 className="font-outfit text-xl font-semibold lowercase text-[#1a1a1a]">
                {f.title}
              </h3>
              <p className="mt-3 text-[15px] leading-relaxed text-[#8e8e8e]">{f.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* how it works */}
      <section id="how-it-works" className="relative z-10 bg-bg-base pb-28 scroll-mt-28">
        <div className="max-w-7xl mx-auto px-8 md:px-16 lg:px-20">
          <h2 className="font-outfit text-3xl md:text-4xl font-semibold tracking-tight lowercase text-[#1a1a1a]">
            how it works
          </h2>
          <div className="mt-10 grid grid-cols-12 gap-x-4 md:gap-x-8 gap-y-10">
            {STEPS.map((s) => (
              <div key={s.n} className="col-span-12 md:col-span-4">
                <div className="font-outfit text-sm font-semibold text-[#1a1a1a] bg-brand-green inline-flex rounded-full px-3 py-1">
                  {s.n}
                </div>
                <h3 className="mt-4 font-outfit text-lg font-semibold lowercase text-[#1a1a1a]">
                  {s.title}
                </h3>
                <p className="mt-2 text-[15px] leading-relaxed text-[#8e8e8e]">{s.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* get started */}
      <section id="get-started" className="relative z-10 bg-bg-base pb-24 scroll-mt-28">
        <div className="max-w-7xl mx-auto px-8 md:px-16 lg:px-20">
          <div className="bg-[#1a1a1a] rounded-3xl p-10 md:p-16 grid grid-cols-12 gap-x-8 gap-y-10 items-center">
            <div className="col-span-12 md:col-span-7">
              <h2 className="font-outfit text-3xl md:text-4xl font-semibold tracking-tight lowercase text-white">
                watch it trade, <span className="text-brand-green">or run your own.</span>
              </h2>
              <p className="mt-4 text-[15px] leading-relaxed text-white/60 max-w-lg">
                The public verifier needs nothing — open it and audit the agent live. Or
                self-host your own Gridora in five minutes: it starts in paper mode, your
                wallet, your keys, your machine.
              </p>
              <code className="mt-6 inline-block rounded-lg bg-white/10 text-brand-green text-[13px] font-mono px-4 py-2.5">
                docker compose up
              </code>
            </div>
            <div className="col-span-12 md:col-span-5 flex flex-col sm:flex-row md:flex-col gap-3 md:items-end">
              <a
                href="/verifier"
                className="inline-flex items-center justify-center gap-1.5 bg-white text-[#1a1a1a] text-[14px] lowercase rounded-full px-6 py-3 font-medium hover:bg-white/90 transition-colors"
              >
                open the verifier <span aria-hidden>→</span>
              </a>
              <a
                href="https://github.com/yeheskieltame/gridora#quickstart"
                className="inline-flex items-center justify-center gap-1.5 bg-brand-green text-black text-[14px] lowercase rounded-full px-6 py-3 font-medium hover:brightness-95 transition-colors"
              >
                run your own agent <span aria-hidden>→</span>
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* footer */}
      <footer className="relative z-10 bg-bg-base pb-10">
        <div className="max-w-7xl mx-auto px-8 md:px-16 lg:px-20 flex flex-wrap items-center justify-between gap-4 border-t border-black/[0.06] pt-6 text-[12px] lowercase text-[#1a1a1a]/60">
          <span>© 2026 gridora — built on bnb chain</span>
          <nav className="flex flex-wrap items-center gap-5">
            <a href="/verifier" className="hover:text-[#1a1a1a] transition-colors">verifier</a>
            <a href="/console" className="hover:text-[#1a1a1a] transition-colors">console</a>
            <a href="https://github.com/yeheskieltame/gridora" className="hover:text-[#1a1a1a] transition-colors">github</a>
            <a href="https://www.8004scan.io/agents" className="hover:text-[#1a1a1a] transition-colors">erc-8004 #140004</a>
          </nav>
        </div>
      </footer>
    </>
  );
}
