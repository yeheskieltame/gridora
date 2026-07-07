import "./globals.css";
import type { Metadata } from "next";
import { Outfit } from "next/font/google";
import { Background } from "@/components/Background";

const outfit = Outfit({ subsets: ["latin"], variable: "--font-outfit", display: "swap" });

export const metadata: Metadata = {
  title: "Gridora · every trade on-chain, read it yourself",
  description:
    "The public trading record of an autonomous agent on BNB Chain. Every settled trade is signed locally via the Trust Wallet Agent Kit and journaled on-chain under ERC-8004 identity #140004 — no dashboard screenshots, no trust required.",
  openGraph: {
    title: "Gridora · every trade on-chain, read it yourself",
    description:
      "The public, verifiable trading record of an autonomous agent on BNB Chain — ERC-8004 identity + append-only TradeJournal + commit→attest StrategyLedger.",
    type: "website",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${outfit.variable} min-h-screen antialiased`}>
        <Background />
        {children}
      </body>
    </html>
  );
}
