import { describe, expect, it } from "vitest";
import { eventLabel } from "./labels.ts";

describe("event labels", () => {
  it("names each message in words staff read", () => {
    expect(eventLabel("investment_booked")).toBe("Investment booked for the client to pay");
    expect(eventLabel("payment_confirmed")).toBe("Payment received");
  });

  it("starts an unknown event's name with a capital, as a sentence", () => {
    expect(eventLabel("something_added_later")).toBe("Something added later");
  });
});
