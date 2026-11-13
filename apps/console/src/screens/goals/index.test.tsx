import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { GoalsError, notConnectedGoalsApi, type GoalsApi } from "./api.ts";
import { GoalsScreen } from "./index.tsx";
import { createStubGoalsApi, sampleGoals } from "./stub.ts";

const TODAY = "2026-11-13";

function renderScreen(api: GoalsApi = createStubGoalsApi(TODAY, 0)) {
  render(<GoalsScreen api={api} />);
  return userEvent.setup();
}

const rows = () => within(screen.getByRole("table", { name: "Client goals" })).getAllByRole("row").slice(1);

describe("goals screen", () => {
  it("counts open goals and lists every goal newest first", async () => {
    renderScreen();
    expect(await screen.findByText("Open goals")).toBeInTheDocument();
    expect(screen.getByText("Set by 3 clients")).toBeInTheDocument();
    expect(screen.getByText("1 with no date")).toBeInTheDocument();
    expect(rows()).toHaveLength(4);
    expect(rows()[0]).toHaveTextContent("Emergency fund");
    expect(rows()[0]).toHaveTextContent("No date");
  });

  it("filters open and closed goals", async () => {
    const user = renderScreen();
    await screen.findByText("Open goals");
    await user.click(screen.getByRole("button", { name: "Closed" }));
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toHaveTextContent("Wedding in August");
    expect(rows()[0]).toHaveTextContent("On 5 Nov 2026");
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(rows()).toHaveLength(3);
  });

  it("shows a goal's progress and the investments it is made of, rollovers included", async () => {
    const user = renderScreen();
    await screen.findByText("Open goals");
    await user.click(screen.getByRole("button", { name: "Plot in Wakiso" }));
    const detail = await screen.findByRole("region", { name: "Goal: Plot in Wakiso" });
    expect(within(detail).getByText("UGX 3,120,400")).toBeInTheDocument();
    expect(within(detail).getByText("Behind")).toBeInTheDocument();
    expect(within(detail).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "15");
    // Once as what awaits payment, once as the saving itself.
    expect(within(detail).getAllByText("UGX 819,000")).toHaveLength(2);
    expect(within(detail).getByText("Awaiting payment", { selector: "span.rounded-pill" })).toBeInTheDocument();
    expect(within(detail).getByText(/rolled over from the last term/)).toBeInTheDocument();
    expect(within(detail).getByText("Matured")).toBeInTheDocument();
  });

  it("offers no way to change, close or move money for a goal", async () => {
    renderScreen();
    await screen.findByText("Open goals");
    const labels = screen.getAllByRole("button").map((button) => button.textContent);
    expect(labels).toEqual(["All", "Open", "Closed", ...sampleGoals(TODAY).map((detail) => detail.goal.name)]);
  });

  it("shows an empty list", async () => {
    renderScreen(createStubGoalsApi(TODAY, 0, "empty"));
    expect(await screen.findByText("No goals yet")).toBeInTheDocument();
  });

  it("says why it could not load, and tries again", async () => {
    let calls = 0;
    const flaky: GoalsApi = {
      ...createStubGoalsApi(TODAY, 0),
      listGoals: (status) => (++calls === 1 ? Promise.reject(new GoalsError("unavailable")) : createStubGoalsApi(TODAY, 0).listGoals(status)),
    };
    const user = renderScreen(flaky);
    expect(await screen.findByText("Goals could not be loaded")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Open goals")).toBeInTheDocument();
  });

  it("says when the console is not connected", async () => {
    renderScreen(notConnectedGoalsApi);
    expect(await screen.findByText("The console is not connected to Finvest yet.")).toBeInTheDocument();
  });
});
