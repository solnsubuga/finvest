/** Always on screen while the sample sign-in is active, so it cannot be mistaken for the real console. */
export function StubBanner() {
  return (
    <p
      role="note"
      className="pointer-events-none fixed right-3 bottom-3 z-50 m-0 rounded-pill border border-warning bg-warning-surface px-3 py-1.5 text-xs font-bold text-warning-text shadow-md"
    >
      Sample sign-in. Not connected to Finvest.
    </p>
  );
}
