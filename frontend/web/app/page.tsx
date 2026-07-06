// Gridora landing — the public product introduction. Fixed light palette (#EDEEF5),
// theme-independent (the verifier/console keep the themed design system).
// The live proof surface moved to /verifier; this page just introduces the product.
import type { Metadata } from "next";
import { Outfit } from "next/font/google";
import { LandingNavbar } from "@/components/landing/Navbar";
import { LandingHero } from "@/components/landing/Hero";
import { LandingSections } from "@/components/landing/Sections";

const outfit = Outfit({ subsets: ["latin"], variable: "--font-outfit", display: "swap" });

export const metadata: Metadata = {
  title: "Gridora · the autonomous trading agent that proves itself",
  description:
    "An autonomous, non-custodial trading agent on BNB Chain. Signs locally via the Trust Wallet Agent Kit, journals every settled trade on-chain under an ERC-8004 identity, and never holds your keys. Watch it live or run your own.",
};

export default function Landing() {
  return (
    <div
      className={`${outfit.variable} relative min-h-screen bg-bg-base text-zinc-900 font-sans antialiased selection:bg-brand-green selection:text-black`}
    >
      <LandingNavbar />
      <main>
        <LandingHero />
        <LandingSections />
      </main>
    </div>
  );
}
