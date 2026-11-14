import { formatExact } from "@/screens/products/format.ts";
import type { Currency, GoalCategory, SavingStatus, Standing } from "./api.ts";

export { formatDay } from "@/screens/automatic-investing/labels.ts";

/** A whole-number string in the currency's smallest unit, as "UGX 1,000,000" or "$500.00". */
export function formatMoney(minor: string, currency: Currency): string {
  return formatExact({ minor: Number(minor), currency });
}

export const CATEGORY_LABELS: Record<GoalCategory, string> = {
  school_fees: "School fees",
  emergency: "Emergency fund",
  land_or_home: "Land or a home",
  business: "Business",
  wedding: "Wedding",
  retirement: "Retirement",
  other: "Something else",
};

export const STANDING_LABELS: Record<Standing, string> = {
  reached: "Reached",
  on_track: "On track",
  behind: "Behind",
  no_date: "No date",
};

export const SAVING_LABELS: Record<SavingStatus, string> = {
  awaiting_payment: "Awaiting payment",
  active: "Running",
  matured: "Matured",
  withdrawn: "Withdrawn early",
  cancelled: "Cancelled",
};
