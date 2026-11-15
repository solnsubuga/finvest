import { Href } from "expo-router";

import { formatDay } from "@/investing/format";
import { describeInvestingError, Investment, InvestingError } from "@/investing/types";

export type Elsewhere = { title: string; message: string; action: { label: string; href: Href; testID: string } | null };

/**
 * What to say when this investment has nothing waiting to be paid out: it may still be running,
 * not yet paid for, never paid for, sent to someone, reversed, or already paid. Only money that
 * was paid out is "on its way".
 */
export function nothingToPayOut(investment: Investment): Elsewhere {
  if (investment.status === "active" && investment.withdrawalRequestedAt) {
    return {
      title: "Being withdrawn",
      message: "You asked to withdraw this investment early. We pay it to the account you chose once the withdrawal goes through, and tell you when it is sent.",
      action: null,
    };
  }
  if (investment.status === "active") {
    const when = investment.maturityDate ? ` on ${formatDay(investment.maturityDate)}` : "";
    return {
      title: "Nothing to pay out yet",
      message: `This investment is still running, so there is nothing to pay out yet. It matures${when}${
        investment.atMaturity === "roll_over" ? " and then starts another term, as you chose" : ", and we pay it then as you chose"
      }.${
        investment.canWithdraw ? " To take it out sooner, withdraw early." : ""
      }`,
      action: investment.canWithdraw
        ? { label: "Withdraw early", href: `/investment/withdraw/${investment.id}`, testID: "pay-out-withdraw" }
        : null,
    };
  }
  if (investment.status === "awaiting_payment") {
    return {
      title: "Nothing to pay out yet",
      message: "This investment has not started yet, so there is nothing to pay out. Pay for it to start earning.",
      action: { label: "Pay now", href: `/pay/${investment.id}`, testID: "pay-out-pay" },
    };
  }
  if (investment.status === "cancelled") {
    return {
      title: "Nothing to pay out",
      message: "This investment was not paid for in time, so it never started and holds no money. You can start a new one.",
      action: { label: "Invest again", href: `/invest/${investment.productCode}`, testID: "pay-out-invest-again" },
    };
  }
  if (investment.status === "transferred") {
    return {
      title: "Nothing to pay out",
      message: "You sent this investment to another Finvest client, so it holds no money now. What you kept, if any, is in a new investment on the same terms.",
      action: { label: "See your investments", href: "/portfolio", testID: "pay-out-portfolio" },
    };
  }
  if (investment.status === "unwound") {
    // The API never sends one to a client; said plainly in case a link to it is opened.
    return {
      title: "Nothing to pay out",
      message: "Finvest reversed this investment, so it holds no money. If any of your money is to come back to you, we tell you when it is sent.",
      action: null,
    };
  }
  return { title: investment.status === "withdrawn" ? "Withdrawn early" : "Already paid out", message: describeInvestingError(new InvestingError("already_sent")), action: null };
}
