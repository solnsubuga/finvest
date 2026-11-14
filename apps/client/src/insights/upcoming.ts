import { Upcoming } from "./types";

/** The screen a coming up row opens. */
export function upcomingHref(item: Upcoming): string {
  switch (item.kind) {
    case "maturity":
      return `/investment/${item.investmentId}`;
    case "goal_saving":
      return item.goalId ? `/goals/${item.goalId}` : `/autoinvest/${item.planId}`;
    case "autoinvest_run":
      return `/autoinvest/${item.planId}`;
    case "goal_date":
      return `/goals/${item.goalId}`;
  }
}

/**
 * What the row says the day holds, one line under it, and a caption under the
 * amount when the amount needs one.
 */
export function upcomingWords(item: Upcoming): { title: string; detail: string; amountNote?: string } {
  switch (item.kind) {
    case "maturity":
      return {
        title: `${item.productName} matures`,
        detail:
          item.atMaturity === "pay_out"
            ? "Pays out to your account"
            : item.atMaturity === "pay_interest"
              ? "Pays the interest, keeps the rest invested"
              : "Rolls over and keeps earning",
        // Only the interest is paid out, so say the amount shown is the whole value.
        ...(item.atMaturity === "pay_interest" ? { amountNote: "Total, interest included" } : {}),
      };
    case "goal_saving":
      return {
        title: `Monthly saving for ${item.goalName ?? "your goal"}`,
        detail: `Into ${item.productName}, from your mobile money`,
      };
    case "autoinvest_run":
      return {
        title: "Automatic investment",
        detail: `Into ${item.productName}, from your mobile money`,
      };
    case "goal_date":
      return { title: `${item.goalName} is due`, detail: "Your goal's target" };
  }
}
