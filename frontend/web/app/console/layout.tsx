import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Gridora · console",
  description:
    "Operator console for the Gridora grid agent — live state for everyone, controls only for the wallet that owns the agent.",
};

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return children;
}
