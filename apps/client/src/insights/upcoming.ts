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

/** What the row says the day holds, and one line under it. */
export function upcomingWords(item: Upcoming): { title: string; detail: string } {
  switch (item.kind) {
    case "maturity":
      return {
        title: `${item.productName} matures`,
        detail:
          item.atMaturity === "pay_out" ? "Pays out to your account" : "Rolls over and keeps earning",
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
