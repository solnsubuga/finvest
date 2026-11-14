import { useEffect, useState } from "react";
import { catalogueErrorMessage, type CatalogueApi, type ProductReach } from "./api.ts";

/** What kind of change the counts are read for, which decides what they mean. */
export type ReachFor = "rate" | "terms" | "pause" | "open";

const COUNT = new Intl.NumberFormat("en-GB");

function plural(count: number, one: string, many: string): string {
  return `${COUNT.format(count)} ${count === 1 ? one : many}`;
}

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; reach: ProductReach };

/**
 * Who a change to a product reaches: the investments that keep their terms, and the goals and
 * plans whose new money takes the change. Counts only. Read when shown, so it is current; give it
 * a `key` of the product code where the product can change under it.
 */
export function Reach({ api, productCode, change }: { api: CatalogueApi; productCode: string; change: ReachFor }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });

  useEffect(() => {
    let current = true;
    api.reach(productCode).then(
      (reach) => current && setLoad({ state: "ready", reach }),
      (error: unknown) => current && setLoad({ state: "failed", message: catalogueErrorMessage(error) }),
    );
    return () => {
      current = false;
    };
  }, [api, productCode]);

  return (
    <section aria-label="Clients affected" className="flex flex-col gap-2 rounded-lg bg-surface-sunken p-4">
      <p className="m-0 text-xs font-bold text-muted">Clients affected</p>
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-sm text-muted">
          Counting who this reaches
        </p>
      ) : load.state === "failed" ? (
        <p className="m-0 text-sm text-muted">Could not count who this reaches. {load.message}</p>
      ) : (
        <Counts reach={load.reach} change={change} />
      )}
    </section>
  );
}

function Counts({ reach, change }: { reach: ProductReach; change: ReachFor }) {
  const plans = reach.activePlans + reach.pausedPlans;
  const kept = reach.runningInvestments + reach.awaitingPayment;
  const headline =
    change === "open"
      ? "New investments only"
      : kept === 0
        ? "Nobody holds it yet"
        : `${plural(reach.clients, "client keeps", "clients keep")} their ${change === "rate" ? "rate" : "terms"}`;
  const newMoney =
    change === "pause"
      ? `While it is paused, payments from ${plural(reach.openGoals, "open goal", "open goals")} and ${plural(plans, "automatic investing plan", "automatic investing plans")} into it cannot start an investment.`
      : change === "open"
        ? "Once it opens, clients can start investments in it and choose it for goals and automatic investing."
        : `New money from ${plural(reach.openGoals, "open goal", "open goals")} and ${plural(plans, "automatic investing plan", "automatic investing plans")} takes the change.`;
  return (
    <>
      <p className="m-0 text-[20px] font-extrabold tracking-[-0.4px]">{headline}</p>
      {change === "open" ? null : (
        <p className="m-0 text-sm leading-[1.45] text-muted">
          {plural(reach.runningInvestments, "running investment", "running investments")} and{" "}
          {plural(reach.awaitingPayment, "investment waiting for payment", "investments waiting for payment")} keep the{" "}
          {change === "rate" ? "rate" : "terms"} they were quoted.
        </p>
      )}
      <p className="m-0 text-sm leading-[1.45] text-muted">{newMoney}</p>
    </>
  );
}
