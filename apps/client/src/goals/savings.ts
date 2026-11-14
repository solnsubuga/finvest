import { formatDay } from "@/payments/format";

import { Saving } from "./types";

/** What a saving that is no longer waiting for payment is doing now. */
export function savingLine(saving: Saving): string {
  switch (saving.status) {
    case "withdrawn":
      return "Taken out early. No longer counts towards this goal";
    case "transferred":
      return "Sent to another client. No longer counts towards this goal";
    case "matured":
      return saving.maturityDate ? `Matured ${formatDay(saving.maturityDate)}` : "Matured";
    default:
      return saving.maturityDate
        ? `Started ${formatDay(saving.startDate ?? "")}, matures ${formatDay(saving.maturityDate)}`
        : "Running";
  }
}
