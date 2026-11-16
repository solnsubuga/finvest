import { useEffect, useState } from "react";

import { QUOTE_DELAY_MS } from "@/investing/useQuote";

import { Amount, Bond, BondQuote, BondsClient, describeBondsError } from "./types";

export type BondCalc =
  | { state: "idle" }
  | { state: "working" }
  | { state: "failed"; message: string }
  | { state: "ready"; quote: BondQuote };

/**
 * What a face value of a bond costs today and pays to maturity, from the backend's quote, asked
 * for once typing pauses. The last answer is kept with the request it answers, so a stale quote
 * is never shown for a newer face value.
 */
export function useBondQuote(client: BondsClient, bond: Bond | undefined, face: Amount | null): BondCalc {
  const [answer, setAnswer] = useState<{ key: string; calc: BondCalc } | null>(null);
  const key = bond && face ? `${bond.code}:${face.currency}:${face.minor}` : null;

  useEffect(() => {
    if (!key || !bond || !face) {
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      client.quote(bond.code, face).then(
        (quote) => live && setAnswer({ key, calc: { state: "ready", quote } }),
        (error: unknown) => live && setAnswer({ key, calc: { state: "failed", message: describeBondsError(error) } }),
      );
    }, QUOTE_DELAY_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // The key holds the bond and the face value by value, so the same figure is not quoted twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, key]);

  return !key ? { state: "idle" } : answer?.key === key ? answer.calc : { state: "working" };
}
