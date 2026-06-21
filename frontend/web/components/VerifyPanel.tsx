"use client";

// "Verify this yourself" — a collapsed disclosure with the literal cast calls a skeptical
// reader can run against BSC. Closed by default to keep the page lean.
import { useState } from "react";
import { ChevronRight } from "./icons";
import { ADDR } from "@/lib/contracts";

export function VerifyPanel({
  agentId,
  owner,
  rpcUrl,
}: {
  agentId: bigint;
  owner: `0x${string}` | null;
  rpcUrl: string;
}) {
  const [open, setOpen] = useState(false);
  const who = owner ?? "<agent-wallet>";

  const castCmd = `# 1 · identity (ERC-8004 soulbound) — owner, trading wallet, registration URI
cast call ${ADDR.identity} \\
  "agentInfo(uint256)(address,address,string)" ${agentId.toString()} \\
  --rpc-url ${rpcUrl}

# 2 · settled-trade count (O(1) counter — works on any RPC)
cast call ${ADDR.journal} \\
  "tradeCountByAgent(uint256)(uint256)" ${agentId.toString()} \\
  --rpc-url ${rpcUrl}
#   …and the per-trade detail (needs a log-serving RPC):
cast logs --address ${ADDR.journal} \\
  "Recorded(uint256,uint256,bytes32,int256,uint64)" \\
  --rpc-url ${rpcUrl}

# 3 · strategy pre-commitment — config pinned BEFORE trading, outcome attested AFTER
cast call ${ADDR.ledger} \\
  "isAttested(address,bytes32)(bool)" ${who} <instanceId> \\
  --rpc-url ${rpcUrl}`;

  return (
    <section aria-label="Verification" className="border-y border-line bg-surface">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="mx-auto flex w-full max-w-6xl cursor-pointer items-center justify-between gap-6 px-6 py-6 text-left transition-colors hover:bg-bg sm:px-10"
      >
        <div className="flex items-baseline gap-4">
          <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-accent">§</span>
          <span className="font-serif text-xl italic text-fg">Verify this yourself.</span>
        </div>
        <ChevronRight className={`h-4 w-4 text-faint transition-transform duration-200 ${open ? "rotate-90 text-accent" : ""}`} />
      </button>

      <div className="expand-enter" data-open={open}>
        <div>
          <div className="mx-auto max-w-6xl px-6 pb-10 sm:px-10">
            <div className="grid grid-cols-1 gap-10 lg:grid-cols-[1fr_1.2fr]">
              <div className="space-y-4 text-sm leading-relaxed text-muted">
                <p>
                  The agent&apos;s identity is a <span className="text-fg">soulbound ERC-8004 NFT</span>: the
                  tokenId is the agent&apos;s ID, ownership is the right to act as it, and it can&apos;t be transferred.
                </p>
                <p>
                  Every settled trade is a <span className="font-mono text-fg">Recorded</span> event on the
                  TradeJournal, gated by <span className="font-mono text-fg">ownerOf(agentId)</span> — only the
                  agent&apos;s wallet can append. Nobody else can fake the record.
                </p>
                <p>
                  Before each grid goes live the agent pins its config hash to the{" "}
                  <span className="font-mono text-fg">StrategyLedger</span> and attests the outcome after —{" "}
                  <span className="text-fg">committedAt &lt; attestedAt</span>, a timestamped pre-commitment it can&apos;t backdate.
                </p>
                <p className="text-muted">
                  Keys never leave the <span className="text-fg">Trust Wallet Agent Kit</span> — the agent
                  signs locally, self-custodial. Read the chain with the{" "}
                  <a href="https://book.getfoundry.sh/reference/cast/cast-call" target="_blank" rel="noopener noreferrer" className="link-quiet text-fg hover:text-accent">cast</a>{" "}
                  calls on the right. No API keys, no trust required.
                </p>
              </div>
              <pre className="overflow-x-auto rounded-xl border border-line bg-bg p-4 font-mono text-[12px] leading-relaxed text-fg">
                <code>{castCmd}</code>
              </pre>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
