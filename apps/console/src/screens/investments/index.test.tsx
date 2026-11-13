import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { InvestmentsError, notConnectedInvestmentsApi, type InvestmentsApi } from "./api.ts";
import { InvestmentsScreen } from "./index.tsx";
import { createStubInvestmentsApi } from "./stub.ts";

const TODAY = "2026-11-13";

function renderInvestments(api: InvestmentsApi = createStubInvestmentsApi()) {
  render(<InvestmentsScreen api={api} today={() => TODAY} />);
  return userEvent.setup();
}

function rows(): string[][] {
  const table = screen.getByRole("table", { name: "Investments" });
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => Array.from(row.querySelectorAll("th, td"), (cell) => cell.textContent ?? ""));
}

describe("investments screen", () => {
  it("lists running investments with net interest and the tax behind it", async () => {
    renderInvestments();
    await screen.findByRole("table", { name: "Investments" });
    expect(screen.getByRole("tab", { name: "Running 5" })).toHaveAttribute("aria-selected", "true");
    const [first] = rows();
    expect(first).toEqual([
      "-ClientSample0000001",
      "91 Day Treasury Bill12.5% before 15% tax",
      "UGX 5,000,000",
      "UGX 123,696UGX 145,521 less UGX 21,825 tax, to 13 Nov 2026",
      "19 Nov 2026",
      "Roll over",
    ]);
    expect(rows().find((row) => row[1]?.startsWith("USD"))?.[4]).toBe("Open term");
  });

  it("shows why matured money is waiting", async () => {
    const user = renderInvestments();
    await user.click(await screen.findByRole("tab", { name: "Matured 2" }));
    expect(rows().map((row) => row[5])).toEqual(["Waiting for the client to choose where to be paid", "Rolled over into a new term"]);
  });

  it("shows the last interest run and its errors", async () => {
    renderInvestments();
    const run = await screen.findByRole("region", { name: "Daily interest run" });
    expect(within(run).getByText("Investments, UGX").nextElementSibling).toHaveTextContent("1,284");
    expect(within(run).getByText("UGX 3,500,640")).toBeInTheDocument();
    expect(within(run).getByText("$68.21")).toBeInTheDocument();
    const history = screen.getByRole("table", { name: "Interest run history" });
    const late = within(history).getAllByRole("row").find((row) => row.textContent?.startsWith("11 Nov 2026UGX"));
    expect(late?.textContent).toContain("UGX 3,484,830");
  });

  it("lists what matures in the next seven days", async () => {
    renderInvestments();
    const week = await screen.findByRole("region", { name: "Maturing this week" });
    expect(within(week).getByText("19 Nov 2026, 91 Day Treasury Bill")).toBeInTheDocument();
    expect(within(week).getByText("UGX 5,000,000, roll over")).toBeInTheDocument();
    // 22 November is nine days away.
    expect(within(week).queryByText(/22 Nov 2026/)).toBeNull();
  });

  it("explains an empty system", async () => {
    renderInvestments(createStubInvestmentsApi("empty"));
    expect(await screen.findByText("No investments are running.")).toBeInTheDocument();
    expect(screen.getByText(/The run has not happened yet/)).toBeInTheDocument();
    expect(screen.getByText("Nothing matures in the next seven days.")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Interest run history" })).toBeNull();
  });

  it("offers to try again when it cannot load", async () => {
    let calls = 0;
    const stub = createStubInvestmentsApi();
    const flaky: InvestmentsApi = {
      ...stub,
      listInvestments: (status) => (++calls === 1 ? Promise.reject(new InvestmentsError("unavailable")) : stub.listInvestments(status)),
    };
    const user = renderInvestments(flaky);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("We could not reach Finvest");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: "Investments" })).toBeInTheDocument();
  });

  it("says when it is not connected", async () => {
    renderInvestments(notConnectedInvestmentsApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected to the Finvest service yet");
  });
});
