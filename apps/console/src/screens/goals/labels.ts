import type { GoalCategory, SavingStatus, Standing } from "./api.ts";

export { formatDay, formatShillings } from "@/screens/automatic-investing/labels.ts";

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
  cancelled: "Cancelled",
};
