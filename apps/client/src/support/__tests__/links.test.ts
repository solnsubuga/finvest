import { hrefFor } from "../links";

describe("hrefFor", () => {
  it("opens the goal a goal notification names", () => {
    expect(hrefFor({ kind: "goal", id: "G1" })).toEqual({
      pathname: "/goals/[goalId]",
      params: { goalId: "G1" },
    });
  });

  it("opens nothing for a notification with no link", () => {
    expect(hrefFor(null)).toBeNull();
  });
});
