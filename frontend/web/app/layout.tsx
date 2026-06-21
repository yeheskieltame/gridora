import "./globals.css";
import type { Metadata } from "next";
import { Background } from "@/components/Background";

export const metadata: Metadata = {
  title: "Gridora · verifiable grid agent on BNB Chain",
  description:
    "Non-custodial adaptive-grid trading agent on BNB Smart Chain. Keys never leave the Trust Wallet Agent Kit; every settled trade is mirrored on-chain via an ERC-8004 identity + TradeJournal. No dashboard, no custody, no trust required.",
  openGraph: {
    title: "Gridora · verifiable grid agent on BNB Chain",
    description:
      "Every settled trade verifiably on-chain. ERC-8004 identity + append-only TradeJournal + commit→attest StrategyLedger. Read it without permission.",
    type: "website",
  },
};

// Apply the saved theme BEFORE first paint (no flash). Default = light (clean/readable);
// honor a previous toggle, else the OS preference.
const THEME_SCRIPT = `(function(){try{
  var t=localStorage.getItem('gridora-theme');
  if(!t){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}
  if(t==='dark')document.documentElement.classList.add('dark');
}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-screen antialiased">
        <Background />
        {children}
      </body>
    </html>
  );
}
