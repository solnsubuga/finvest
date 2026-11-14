import { useEffect, useState } from "react";

import { Amount, describeInvestingError, InvestingClient, Product, Quote } from "./types";

/** How long typing must pause before the quote is asked for. */
export const QUOTE_DELAY_MS = 400;

export type Calc = { state: "idle" } | { state: "working" } | { state: "failed"; message: string } | { state: "ready"; quote: Quote };

/**
 * What an amount would earn in a product, from the backend's quote, asked for
 * once typing pauses. The last answer is kept with the request it answers, so
 * a stale quote is never shown for a newer amount.
 */
export function useQuote(client: InvestingClient, product: Product | undefined, amount: Amount | null): Calc {
  const [answer, setAnswer] = useState<{ key: string; calc: Calc } | null>(null);
  const key = product && amount ? `${product.code}:${amount.currency}:${amount.minor}` : null;

  useEffect(() => {
    if (!key || !product || !amount) {
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      client.quote(product.code, amount).then(
        (quote) => live && setAnswer({ key, calc: { state: "ready", quote } }),
        (error: unknown) => live && setAnswer({ key, calc: { state: "failed", message: describeInvestingError(error) } }),
      );
    }, QUOTE_DELAY_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // The key holds the product and the amount by value, so the same figure is not quoted twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, key]);

  return !key ? { state: "idle" } : answer?.key === key ? answer.calc : { state: "working" };
}
