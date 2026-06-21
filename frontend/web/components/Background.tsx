// Decorative page background: slow-drifting aurora blobs (gold + blue) behind everything.
// Fixed, pointer-events-none, theme-aware (gold via --accent). Pairs with the body grid.
export function Background() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
      <div className="aurora-a absolute -top-40 left-1/3 h-[36rem] w-[36rem] rounded-full bg-accent/20 blur-[130px]" />
      <div className="aurora-b absolute top-1/4 -right-24 h-[32rem] w-[32rem] rounded-full bg-sky-500/15 blur-[130px]" />
      <div className="aurora-c absolute -bottom-40 left-0 h-[30rem] w-[30rem] rounded-full bg-accent/12 blur-[120px]" />
    </div>
  );
}
