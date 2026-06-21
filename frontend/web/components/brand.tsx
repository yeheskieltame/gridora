// Brand marks for the "Built on" strip. Trust Wallet is the official logo (via 21st.dev
// logo registry); the BNB mark is a clean diamond cluster in BNB gold.

export function TrustWalletMark({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 444 501" className={className} fill="none" aria-hidden>
      <path d="M0.71 72.41 222.16 0.11V500.63C63.98 433.89 0.71 305.98 0.71 233.69V72.41Z" fill="#0500FF" />
      <path d="M443.62 72.41 222.17 0.11V500.63C380.35 433.89 443.62 305.98 443.62 233.69V72.41Z" fill="url(#twg)" />
      <defs>
        <linearGradient id="twg" x1="385.26" y1="-34.78" x2="216.61" y2="493.5" gradientUnits="userSpaceOnUse">
          <stop offset="0.02" stopColor="#0000FF" /><stop offset="0.08" stopColor="#0094FF" />
          <stop offset="0.16" stopColor="#48FF91" /><stop offset="0.42" stopColor="#0094FF" />
          <stop offset="0.68" stopColor="#0038FF" /><stop offset="0.9" stopColor="#0500FF" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export function BnbMark({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="#F0B90B" aria-hidden>
      <polygon points="12,3.6 14.4,6 12,8.4 9.6,6" />
      <polygon points="12,15.6 14.4,18 12,20.4 9.6,18" />
      <polygon points="6,9.6 8.4,12 6,14.4 3.6,12" />
      <polygon points="18,9.6 20.4,12 18,14.4 15.6,12" />
      <polygon points="12,9 15,12 12,15 9,12" />
    </svg>
  );
}
