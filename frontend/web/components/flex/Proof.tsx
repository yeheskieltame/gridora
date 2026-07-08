// Proof section (server component): the identity + the three contracts that make
// the history unfakeable, plus the commit→trade→attest explainer.
import { Address } from "@/components/Address";
import { ArrowUpRight } from "@/components/icons";
import { ADDR, COMPETITION, WIN } from "@/lib/contracts";
import type { Agent } from "@/lib/data";

const CONTRACTS: Array<{ name: string; addr: string; what: string }> = [
  { name: "identityregistry", addr: ADDR.identity, what: "soulbound erc-721, one identity per agent, gates the journal" },
  { name: "tradejournal", addr: ADDR.journal, what: "append-only event log of every settled episode, O(1), can't be edited" },
  { name: "strategyledger", addr: ADDR.ledger, what: "config hash committed before trading, outcome attested after" },
];

const STEPS = [
  { n: "01", title: "commit", body: "the strategy config hash goes on-chain before a single order, so nobody can rewrite the plan after the fact." },
  { n: "02", title: "trade", body: "orders are signed locally by the trust wallet agent kit and settle on bnb chain. keys never leave the machine." },
  { n: "03", title: "attest", body: "each settled episode is journaled (the tape above) and the outcome hash attested, so anyone can replay the math." },
];

export function Proof({ agent }: { agent: Agent }) {
  return (
    <>
      <section id="proof" className="relative z-10 bg-bg pb-20 scroll-mt-28">
        <div className="max-w-7xl mx-auto px-6 md:px-16 lg:px-20">
          <h2 className="font-display text-3xl md:text-4xl font-semibold tracking-tight lowercase text-fg">
            proof, not promises
          </h2>
          <div className="mt-8 grid grid-cols-12 gap-4 md:gap-6">
            <div className="col-span-12 lg:col-span-5 surface-card card-grid p-7">
              <div className="flex items-center justify-between">
                <span className="chip-volt px-3 py-1 text-[12px]">🥉 {WIN.place}</span>
                <a href="https://www.8004scan.io/agents" target="_blank" rel="noreferrer"
                   className="link-quiet inline-flex items-center gap-1 text-[12px] lowercase text-accent">
                  8004scan <ArrowUpRight className="w-3 h-3" />
                </a>
              </div>
              <h3 className="mt-5 font-display text-lg font-semibold lowercase text-fg">
                {WIN.event.toLowerCase()}
              </h3>
              <p className="mt-2 text-[13px] leading-relaxed text-muted">
                a soulbound on-chain identity, registered in the competition and journaling
                every trade since. the journal only accepts entries from its holder, so nobody
                else can write this history, including us after the fact.
              </p>
              <dl className="mt-5 space-y-2.5 text-[13px]">
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-faint lowercase shrink-0">erc-8004 identity</dt>
                  <dd>
                    <a href={`${COMPETITION.scan}/tx/${COMPETITION.identityTx}`} target="_blank" rel="noreferrer"
                       className="link-quiet inline-flex items-center gap-1 font-mono text-fg">
                      #{COMPETITION.agentId} <ArrowUpRight className="w-3 h-3 text-accent" />
                    </a>
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-faint lowercase shrink-0">competition entry</dt>
                  <dd>
                    <a href={`${COMPETITION.scan}/tx/${COMPETITION.registrationTx}`} target="_blank" rel="noreferrer"
                       className="link-quiet inline-flex items-center gap-1 font-mono text-fg">
                      registration tx <ArrowUpRight className="w-3 h-3 text-accent" />
                    </a>
                  </dd>
                </div>
                {agent.wallet && (
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-faint lowercase shrink-0">trading wallet</dt>
                    <dd><Address value={agent.wallet} /></dd>
                  </div>
                )}
                {agent.owner && (
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-faint lowercase shrink-0">owner</dt>
                    <dd><Address value={agent.owner} /></dd>
                  </div>
                )}
              </dl>
            </div>

            <div className="col-span-12 lg:col-span-7 grid gap-4 md:gap-6">
              {CONTRACTS.map((c) => (
                <div key={c.name} className="surface-card lift px-7 py-5 flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="font-display text-[15px] font-semibold lowercase text-fg">{c.name}</h3>
                    <p className="mt-1 text-[12.5px] text-muted">{c.what}</p>
                  </div>
                  <Address value={c.addr as `0x${string}`} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section id="how" className="relative z-10 bg-bg pb-24 scroll-mt-28">
        <div className="max-w-7xl mx-auto px-6 md:px-16 lg:px-20">
          <div className="grid grid-cols-12 gap-x-4 md:gap-x-8 gap-y-10">
            {STEPS.map((s) => (
              <div key={s.n} className="col-span-12 md:col-span-4">
                <div className="chip-volt px-3 py-1 text-[12px] font-display">{s.n}</div>
                <h3 className="mt-4 font-display text-lg font-semibold lowercase text-fg">{s.title}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-muted">{s.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
