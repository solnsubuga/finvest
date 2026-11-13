import { useState } from "react";

/** The time the screen was opened, read once so a re-render never changes what a date says. */
export function useNow(): number {
  const [now] = useState(() => Date.now());
  return now;
}
