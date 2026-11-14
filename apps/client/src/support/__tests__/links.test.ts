import { hrefFor } from "../links";

describe("hrefFor", () => {
  it("opens the company or group account a notification names", () => {
    expect(hrefFor({ kind: "organisation", id: "O1" })).toEqual({
      pathname: "/account/organisations/[organisationId]",
      params: { organisationId: "O1" },
    });
  });

  it("opens the goal a goal notification names", () => {
    expect(hrefFor({ kind: "goal", id: "G1" })).toEqual({
      pathname: "/goals/[goalId]",
      params: { goalId: "G1" },
    });
  });

  it("opens the plan an automatic investing notification names", () => {
    expect(hrefFor({ kind: "autoinvest_plan", id: "P1" })).toEqual({
      pathname: "/autoinvest/[planId]",
      params: { planId: "P1" },
    });
  });

  it("opens nothing for a notification with no link", () => {
    expect(hrefFor(null)).toBeNull();
  });
});
