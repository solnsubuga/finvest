import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { MaturitiesError, notConnectedMaturitiesApi, type MaturitiesApi } from "./api.ts";
import { MaturitiesScreen } from "./index.tsx";
import { createStubMaturitiesApi } from "./stub.ts";

function renderMaturities(api: MaturitiesApi = createStubMaturitiesApi("sample", 0)) {
  render(
    <MemoryRouter>
      <MaturitiesScreen api={api} directory={createStubDirectory()} />
    </MemoryRouter>,
  );
  return userEvent.setup();
}

function rows(name: string): string[][] {
  return within(screen.getByRole("table", { name }))
    .getAllByRole("row")
    .slice(1)
    .map((row) => Array.from(row.querySelectorAll("th, td"), (cell) => cell.textContent ?? ""));
}

describe("maturities screen", () => {
  it("warns about a term the run has not closed, with its client and how late it is", async () => {
    renderMaturities();
    const run = await screen.findByRole("region", { name: "Maturity run" });
    expect(run).toHaveTextContent("1 term past its maturity date is still running");
    await screen.findByRole("link", { name: "Peter Okello" });
    expect(rows("Terms past their maturity date that are still running")).toEqual([
      ["Peter Okello1001005130", "91 Day Treasury Bill", "UGX 1,000,000", "12 Nov 20262 days ago", "Pay out"],
    ]);
    expect(screen.getByRole("link", { name: "Peter Okello" })).toHaveAttribute("href", "/clients?client=sample-client-peter");
  });

  it("shows money held for the client, after tax, with why it waits and what to do", async () => {
    renderMaturities();
    const held = await screen.findByRole("region", { name: "Held for the client" });
    expect(within(held).getByText("UGX 1,026,490")).toBeInTheDocument();
    const [row] = rows("Matured terms whose money waits for the client");
    expect(row?.[2]).toBe("UGX 1,026,490UGX 1,000,000 and UGX 26,490 interest");
    expect(row?.[4]).toContain("Chose to be paid, but never said where");
    expect(row?.[4]).toContain("choose a payout account in the app");
  });

  it("lists each day's terms with what became of them and what is still to close", async () => {
    renderMaturities();
    await screen.findByRole("table", { name: /Terms ending each day/ });
    const days = rows("Terms ending each day, from a week ago to two weeks ahead");
    expect(days.find((row) => row[0] === "11 Nov 2026" && row[1] === "UGX")).toEqual([
      "11 Nov 2026",
      "UGX",
      "8",
      "UGX 27,000,000",
      "6 rolled over, 1 paid out, 1 held",
      "None",
    ]);
    expect(days.find((row) => row[0]?.startsWith("14 Nov 2026"))).toEqual([
      "14 Nov 2026Today",
      "UGX",
      "7",
      "UGX 22,800,000",
      "Nothing closed yet",
      "5 to roll over, 2 to pay out",
    ]);
    expect(days.find((row) => row[0] === "19 Nov 2026" && row[1] === "USD")?.[3]).toBe("$30,000.00");
  });

  it("says when nothing is due and nothing needs a person", async () => {
    renderMaturities(createStubMaturitiesApi("empty", 0));
    expect(await screen.findByText("Every term that ended before today has been closed.")).toBeInTheDocument();
    expect(screen.getByText(/No money is waiting/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "No term ends this week or the next two" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("offers to try again when the read fails", async () => {
    let calls = 0;
    const api: MaturitiesApi = {
      getMaturities: () => (calls++ === 0 ? Promise.reject(new MaturitiesError("unavailable")) : createStubMaturitiesApi("sample", 0).getMaturities()),
    };
    const user = renderMaturities(api);
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Maturity run" })).toBeInTheDocument();
  });

  it("says when it is not connected", async () => {
    renderMaturities(notConnectedMaturitiesApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("Maturities are not connected to the Finvest service yet.");
  });
});
