import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CatalogueError, notConnectedCatalogueApi, type CatalogueApi } from "../products/api.ts";
import { createStubCatalogueApi } from "../products/stub.ts";
import { RatesScreen } from "./index.tsx";

const TODAY = "2026-11-13";
const ME = "-StaffSampleMe000001";

function renderRates(api: CatalogueApi = createStubCatalogueApi({ me: ME, today: TODAY }), me = ME) {
  render(<RatesScreen api={api} me={me} canChange today={() => TODAY} />);
  return { api, user: userEvent.setup() };
}

const waitingCard = () => screen.findByRole("region", { name: "91 Day Treasury Bill" });

function historyRows(): string[][] {
  const table = screen.getByRole("table", { name: "Rate history" });
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => Array.from(row.querySelectorAll("th, td"), (cell) => cell.textContent ?? ""));
}

describe("rates screen", () => {
  it("shows the rate history with what is in force and what was replaced", async () => {
    renderRates();
    await waitingCard();
    expect(historyRows()).toEqual([
      ["91 Day Treasury Bill", "12.5%", "15%", "10.63%", "1 Jan 2026", "In force"],
      ["182 Day Treasury Bill", "13.75%", "15%", "11.69%", "1 Jul 2026", "In force"],
      ["182 Day Treasury Bill", "14%", "15%", "11.90%", "1 Jan 2026", "Replaced"],
      ["USD Fixed Income", "7.25%", "5%", "6.89%", "1 Jan 2026", "In force"],
    ]);
  });

  it("lets a second person approve a colleague's change", async () => {
    const { user } = renderRates();
    const card = await waitingCard();
    expect(within(card).getByText("Change waiting for you")).toBeInTheDocument();
    expect(within(card).getByText("12.5% to 13%")).toBeInTheDocument();
    expect(within(card).getByText("10.63% to 11.05%")).toBeInTheDocument();
    expect(within(card).getByText("1 Dec 2026")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Approve as second person" }));
    expect(await screen.findByText("Nothing waiting for approval")).toBeInTheDocument();
    expect(historyRows()[0]).toEqual(["91 Day Treasury Bill", "13%", "15%", "11.05%", "1 Dec 2026", "Approved, starts later"]);
  });

  it("sends a change back with a note", async () => {
    const { api, user } = renderRates();
    const card = await waitingCard();
    await user.click(within(card).getByRole("button", { name: "Send back with a note" }));
    expect(within(card).getByRole("button", { name: "Send back" })).toBeDisabled();
    await user.type(within(card).getByLabelText("Why it is going back"), "Wait for the next auction.");
    await user.click(within(card).getByRole("button", { name: "Send back" }));
    expect(await screen.findByText("Nothing waiting for approval")).toBeInTheDocument();
    const [sent] = await api.listProposals("sent_back");
    expect(sent?.decisionNote).toBe("Wait for the next auction.");
  });

  it("lets the proposer only withdraw their own change", async () => {
    const api = createStubCatalogueApi({ me: ME, today: TODAY, sample: "empty" });
    await api.propose({ productCode: "TREA-182", annualRateBps: 1400, taxBps: 1500, effectiveFrom: "2026-12-01", reason: "Auction result" });
    const { user } = renderRates(api);
    const card = await screen.findByRole("region", { name: "182 Day Treasury Bill" });
    expect(within(card).getByText("Waiting for a second person")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Approve as second person" })).toBeNull();
    await user.click(within(card).getByRole("button", { name: "Withdraw this change" }));
    expect(await screen.findByText("Nothing waiting for approval")).toBeInTheDocument();
  });

  it("proposes a change and previews what clients would see", async () => {
    const { api, user } = renderRates(createStubCatalogueApi({ me: ME, today: TODAY, sample: "empty" }));
    const form = await screen.findByRole("form", { name: "Propose a rate change" });
    expect(within(form).getByLabelText("Rate before tax, %")).toHaveValue("12.5");
    await user.clear(within(form).getByLabelText("Rate before tax, %"));
    await user.type(within(form).getByLabelText("Rate before tax, %"), "13.25");
    expect(within(form).getByText("11.26% a year")).toBeInTheDocument();
    await user.type(within(form).getByLabelText("Starts on"), "2026-12-01");
    await user.type(within(form).getByLabelText("Reason, for the audit trail"), "Yield rose at auction.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByText("Waiting for a second person")).toBeInTheDocument();
    const [proposal] = await api.listProposals("proposed");
    expect(proposal).toMatchObject({ productCode: "TREA-91", rate: { annualRateBps: 1325, taxBps: 1500, effectiveFrom: "2026-12-01" } });
  });

  it("checks the form before sending it", async () => {
    const { api, user } = renderRates(createStubCatalogueApi({ me: ME, today: TODAY, sample: "empty" }));
    const form = await screen.findByRole("form", { name: "Propose a rate change" });
    await user.clear(within(form).getByLabelText("Rate before tax, %"));
    await user.type(within(form).getByLabelText("Rate before tax, %"), "12.555");
    await user.type(within(form).getByLabelText("Starts on"), TODAY);
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getByText("Type a rate from 0.01 to 50, such as 12.5.")).toBeInTheDocument();
    expect(within(form).getByText("Pick a day after today.")).toBeInTheDocument();
    expect(within(form).getByText("Say why the rate is changing.")).toBeInTheDocument();
    expect(await api.listProposals()).toEqual([]);
  });

  it("refuses, like the backend, a change to the rate already in force", async () => {
    const stub = createStubCatalogueApi({ me: ME, today: TODAY, sample: "empty" });
    const same = { productCode: "TREA-91", annualRateBps: 1250, taxBps: 1500, effectiveFrom: "2026-12-01", reason: "x" };
    await expect(stub.propose(same)).rejects.toMatchObject({ code: "nothing_changes" });
    await expect(stub.propose({ ...same, taxBps: 1000 })).resolves.toMatchObject({ productCode: "TREA-91" });
  });

  it("shows the backend's refusal", async () => {
    const stub = createStubCatalogueApi({ me: ME, today: TODAY, sample: "empty" });
    const refusing: CatalogueApi = { ...stub, propose: () => Promise.reject(new CatalogueError("date_already_taken")) };
    const { user } = renderRates(refusing);
    const form = await screen.findByRole("form", { name: "Propose a rate change" });
    await user.type(within(form).getByLabelText("Starts on"), "2026-12-01");
    await user.type(within(form).getByLabelText("Reason, for the audit trail"), "Auction result");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("already has a rate starting on that day");
    expect(within(form).getByLabelText("Reason, for the audit trail")).toHaveValue("Auction result");
  });

  it("says when it is not connected", async () => {
    renderRates(notConnectedCatalogueApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected to the Finvest service yet");
  });

  it("only shows rates to someone who may not change them", async () => {
    const api = createStubCatalogueApi({ me: ME, today: TODAY });
    render(<RatesScreen api={api} me={ME} canChange={false} today={() => TODAY} />);
    expect(await screen.findByRole("heading", { name: "You can see rates, not change them" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Propose a rate change" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });
});
