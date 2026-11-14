import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PotTransfersError, type PotTransfersApi } from "./api.ts";
import { parseAmount, PotTransfers } from "./PotTransfers.tsx";
import { createStubPotTransfersApi } from "./stub.ts";

const ME = "-Nstaff000000000000A";
const NOW = new Date("2026-11-14T09:00:00Z");

function renderSection(api: PotTransfersApi = createStubPotTransfersApi(ME, 0, "sample", NOW), canChange = true) {
  let keys = 0;
  render(<PotTransfers api={api} me={ME} canChange={canChange} newKey={() => `key-${++keys}`} today={() => "2026-11-14"} />);
  return userEvent.setup();
}

describe("transfers between pots", () => {
  it("reads amounts in the currency's own decimal places", () => {
    expect(parseAmount("48,000,000", "UGX")).toEqual({ minor: 48_000_000, currency: "UGX" });
    expect(parseAmount("1,500.25", "USD")).toEqual({ minor: 150_025, currency: "USD" });
    expect(parseAmount("1500.5", "USD")).toEqual({ minor: 150_050, currency: "USD" });
    expect(parseAmount("12.5", "UGX")).toBeNull();
    expect(parseAmount("1.234", "USD")).toBeNull();
    expect(parseAmount("abc", "UGX")).toBeNull();
  });

  it("records a transfer that was made, with the provider's fee", async () => {
    const api = createStubPotTransfersApi(ME, 0, "sample", NOW);
    const record = vi.spyOn(api, "record");
    const user = renderSection(api);
    const form = await screen.findByRole("form", { name: "Record a transfer between pots" });
    await user.click(within(form).getByRole("button", { name: "Record transfer" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Choose the pot the money left");

    await user.selectOptions(within(form).getByLabelText("From"), "iotec:mtn");
    await user.selectOptions(within(form).getByLabelText("To"), "bank:trust");
    await user.type(within(form).getByLabelText("Sent (UGX)"), "20,000,000");
    await user.type(within(form).getByLabelText("Arrived (UGX)"), "19,990,000");
    expect(form).toHaveTextContent("Provider's fee: UGX 10,000");
    await user.type(within(form).getByLabelText("Provider or bank reference"), "IOT-SWEEP-3");
    await user.type(within(form).getByLabelText("Reason"), "Afternoon sweep");
    await user.click(within(form).getByRole("button", { name: "Record transfer" }));

    expect(record).toHaveBeenCalledWith("key-1", {
      from: { source: "iotec", accountReference: "mtn" },
      to: { source: "bank", accountReference: "trust" },
      sent: { minor: 20_000_000, currency: "UGX" },
      received: { minor: 19_990_000, currency: "UGX" },
      valueDate: "2026-11-14",
      providerReference: "IOT-SWEEP-3",
      reason: "Afternoon sweep",
    });
    expect(await screen.findByText(/Recorded. A different person/)).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Transfers between pots waiting" })).getAllByRole("article")).toHaveLength(3);
  });

  it("words a refusal of the record", async () => {
    const user = renderSection();
    const form = await screen.findByRole("form", { name: "Record a transfer between pots" });
    await user.selectOptions(within(form).getByLabelText("From"), "bank:trust");
    await user.selectOptions(within(form).getByLabelText("To"), "iotec:mtn");
    await user.type(within(form).getByLabelText("Sent (UGX)"), "1000000");
    await user.type(within(form).getByLabelText("Arrived (UGX)"), "999000");
    await user.type(within(form).getByLabelText("Provider or bank reference"), "BANK-1");
    await user.type(within(form).getByLabelText("Reason"), "Top up the wallet");
    await user.click(within(form).getByRole("button", { name: "Record transfer" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("cannot carry a fee yet");
  });

  it("lets a second person approve, and keeps the key after a lost answer", async () => {
    const api = createStubPotTransfersApi(ME, 0, "sample", NOW);
    const approve = vi.spyOn(api, "approve").mockRejectedValueOnce(new PotTransfersError("unavailable"));
    const user = renderSection(api);
    const card = await screen.findByRole("article", { name: "Transfer of UGX 48,000,000 from ioTec wallet, MTN" });
    expect(card).toHaveTextContent("UGX 15,000");
    await user.click(within(card).getByRole("button", { name: "Approve as second person" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    await user.click(within(card).getByRole("button", { name: "Approve as second person" }));
    expect(await screen.findByText("Approved and posted to the books.")).toBeInTheDocument();
    expect(approve.mock.calls.map((call) => call[0])).toEqual(["key-1", "key-1"]);
  });

  it("declines only with a reason", async () => {
    const api = createStubPotTransfersApi(ME, 0, "sample", NOW);
    const decline = vi.spyOn(api, "decline");
    const user = renderSection(api);
    const card = await screen.findByRole("article", { name: /from ioTec wallet, MTN/ });
    await user.click(within(card).getByRole("button", { name: "Decline" }));
    expect(within(card).getByRole("alert")).toHaveTextContent("Give a reason for declining.");
    await user.type(within(card).getByLabelText("Reason, if you decline"), "Not on the bank statement");
    await user.click(within(card).getByRole("button", { name: "Decline" }));
    expect(decline).toHaveBeenCalledWith("key-1", "-OdPotTransfer000001", "Not on the bank statement");
  });

  it("offers the person who recorded it nothing to press", async () => {
    renderSection();
    const card = await screen.findByRole("article", { name: /from ioTec wallet, Airtel/ });
    expect(card).toHaveTextContent("You recorded this");
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the list only to someone who cannot change reconciliation, and empty and failed lists", async () => {
    const { unmount } = render(<PotTransfers api={createStubPotTransfersApi(ME, 0, "sample", NOW)} me={ME} canChange={false} />);
    await screen.findByRole("list", { name: "Transfers between pots waiting" });
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();
    unmount();
    const empty = render(<PotTransfers api={createStubPotTransfersApi(ME, 0, "empty", NOW)} me={ME} canChange />);
    expect(await screen.findByText("No transfer between pots is waiting for a second person.")).toBeInTheDocument();
    empty.unmount();
    render(<PotTransfers api={createStubPotTransfersApi(ME, 0, "error", NOW)} me={ME} canChange />);
    expect(await screen.findByRole("heading", { name: "Transfers between pots could not be loaded" })).toBeInTheDocument();
  });
});
