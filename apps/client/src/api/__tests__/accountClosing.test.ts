import { toAutoinvestError } from "@/autoinvest/apiAutoinvestClient";
import { describeAutoinvestError } from "@/autoinvest/types";
import { toGoalError } from "@/goals/apiGoalsClient";
import { describeGoalError } from "@/goals/types";
import { toInvestingError } from "@/investing/apiInvestingClient";
import { describeInvestingError } from "@/investing/types";
import { toPaymentError } from "@/payments/apiPaymentsClient";
import { describePaymentError } from "@/payments/types";

import { ApiError, isAccountClosing } from "../http";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

const closing = () => new ApiError("conflict", { status: 409, fields: [{ field: "account", code: "closing" }] });
const otherConflict = () => new ApiError("conflict", { status: 409 });

describe("a request to close the account is waiting", () => {
  it("is told apart from any other conflict", () => {
    expect(isAccountClosing(closing())).toBe(true);
    expect(isAccountClosing(otherConflict())).toBe(false);
    expect(isAccountClosing(new ApiError("validation_failed", { fields: [{ field: "account", code: "closing" }] }))).toBe(false);
  });

  it("says why on a new investment, payment, goal or plan", () => {
    const refusals = [
      describeInvestingError(toInvestingError(closing())),
      describePaymentError(toPaymentError(closing(), "pay")),
      describeGoalError(toGoalError(closing(), "create")),
      describeAutoinvestError(toAutoinvestError(closing(), "create")),
    ];
    for (const text of refusals) {
      expect(text).toMatch(/request to close your account is being checked/);
      expect(text).toMatch(/Keep my account open/);
    }
  });

  it("leaves every other conflict as it was", () => {
    expect(toInvestingError(otherConflict()).code).toBe("too_many_unpaid");
    expect(toPaymentError(otherConflict(), "pay").code).toBe("not_payable");
    expect(toGoalError(otherConflict(), "create").code).toBe("too_many_goals");
    expect(toAutoinvestError(otherConflict(), "create").code).toBe("too_many_plans");
  });
});
