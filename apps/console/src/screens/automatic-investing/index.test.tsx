import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AutoinvestError, notConnectedAutoinvestApi, type AutoinvestApi } from "./api.ts";
import { AutomaticInvestingScreen } from "./index.tsx";
import { addDays, codeLabel, formatDay, formatShillings, runNote, scheduleLabel } from "./labels.ts";
import { createStubAutoinvestApi, sampleOverview } from "./stub.ts";

// 13 Nov 2026 in Kampala, a Friday.
const NOW = () => new Date("2026-11-13T09:00:00Z");

function renderScreen(api: AutoinvestApi = createStubAutoinvestApi(0)) {
  render(<AutomaticInvestingScreen api={api} now={NOW} />);
  return userEvent.setup();
}

const rows = () => within(screen.getByRole("table", { name: "Automatic investing runs this week" })).getAllByRole("row").slice(1);

describe("automatic investing screen", () => {
  it("counts this week's runs and lists them newest first", async () => {
    renderScreen();
    expect(await screen.findByText("Plans running")).toBeInTheDocument();
    expect(screen.getByText("5 paused by the client")).toBeInTheDocument();
    expect(screen.getByText("2 waiting for the client")).toBeInTheDocument();
    expect(screen.getByText("Runs from 7 Nov 2026 to 13 Nov 2026")).toBeInTheDocument();
    expect(screen.getByText("2 failed, 1 missed")).toBeInTheDocument();
    const days = rows().map((row) => within(row).getByRole("rowheader").textContent);
    expect(days).toEqual([...days].sort((a, b) => Date.parse(b ?? "") - Date.parse(a ?? "")));
    expect(rows()).toHaveLength(8);
  });

  it("says why a run failed and when it is tried again", async () => {
    renderScreen();
    await screen.findByText("Plans running");
    const failed = rows().find((row) => row.textContent?.includes("Esther Akello"));
    expect(failed).toHaveTextContent("Payment failed");
    expect(failed).toHaveTextContent("Not enough money on the number. One more try on 14 Nov 2026.");
    const late = rows().find((row) => row.textContent?.includes("Ruth Nambi"));
    expect(late).toHaveTextContent("Not opened on its day, so it was skipped rather than collected late.");
  });

  it("filters by status", async () => {
    const user = renderScreen();
    await screen.findByText("Plans running");
    await user.click(screen.getByRole("button", { name: "Missed" }));
    expect(screen.getByRole("button", { name: "Missed" })).toHaveAttribute("aria-pressed", "true");
    expect(rows()).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Invested" }));
    expect(rows()).toHaveLength(3);
  });

  it("offers no way to start or retry a run", async () => {
    renderScreen();
    await screen.findByText("Plans running");
    const buttons = screen.getAllByRole("button").map((button) => button.textContent);
    expect(buttons).toEqual(["All", "Due", "Prompt sent", "Invested", "Payment failed", "Missed"]);
  });

  it("shows an empty week", async () => {
    renderScreen(createStubAutoinvestApi(0, "empty"));
    expect(await screen.findByText("No runs this week")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows an error with a way to try again", async () => {
    let calls = 0;
    const api: AutoinvestApi = {
      getOverview: (today) => {
        calls += 1;
        return calls === 1 ? Promise.reject(new AutoinvestError("unavailable")) : createStubAutoinvestApi(0).getOverview(today);
      },
    };
    const user = renderScreen(api);
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest. Try again in a moment.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Plans running")).toBeInTheDocument();
  });

  it("says so when the console is not connected", async () => {
    renderScreen(notConnectedAutoinvestApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("The console is not connected to Finvest yet.");
  });
});

describe("automatic investing labels", () => {
  it("formats days, amounts and schedules without fractions or time zones", () => {
    expect(formatDay("2026-11-13")).toBe("13 Nov 2026");
    expect(formatShillings("1000000")).toBe("UGX 1,000,000");
    expect(formatShillings("100")).toBe("UGX 100");
    expect(scheduleLabel("weekly", 5)).toBe("Every Friday");
    expect(scheduleLabel("monthly", 31)).toBe("Monthly on the 31st");
    expect(scheduleLabel("monthly", 12)).toBe("Monthly on the 12th");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("names every reason, and shows an unknown one as it is", () => {
    expect(codeLabel("previous_run_open")).toBe("The plan's previous run was still open");
    expect(codeLabel("something_new")).toBe("something_new");
  });

  it("notes a second try", () => {
    const [paid] = sampleOverview("2026-11-13").runs.filter((run) => run.attempt === 2 && run.status === "paid");
    expect(paid && runNote(paid)).toBe("Paid on the second try, the day after.");
  });
});
