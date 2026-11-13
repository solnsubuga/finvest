import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DealingError, notConnectedDealingApi, type DealingApi } from "./api.ts";
import { formatAuctionDay, formatExact, formatShort, parseAmount } from "./format.ts";
import { DealingDeskScreen } from "./index.tsx";
import { createStubDealingApi } from "./stub.ts";

const ME = { id: "sample-me", name: "Amina" };
const TODAY = "2026-11-13";
const BID_91 = "Auction bid, 91 Day Treasury Bill";
const BID_182 = "Auction bid, 182 Day Treasury Bill";
const PURCHASE_182 = "Secondary market purchase, 182 Day Treasury Bill";

function renderDesk(api: DealingApi = createStubDealingApi(ME, 0, () => TODAY)) {
  let keys = 0;
  render(<DealingDeskScreen api={api} newKey={() => `key-${++keys}`} today={() => TODAY} />);
  return { api, user: userEvent.setup() };
}

const order = (name: string) => screen.findByRole("article", { name });
const figure = (name: string) => screen.getByText(name).parentElement as HTMLElement;

function positionRow(product: string): string[] {
  const row = within(screen.getByRole("table")).getByRole("rowheader", { name: product }).closest("tr") as HTMLElement;
  return within(row)
    .getAllByRole("cell")
    .map((cell) => cell.textContent ?? "");
}

describe("dealing desk screen", () => {
  it("shows the position and the four figures from the canvas", async () => {
    renderDesk();
    await order(BID_91);
    expect(figure("Client money to place")).toHaveTextContent("UGX 386.0m");
    expect(figure("Bills held for clients")).toHaveTextContent("UGX 18,240.0m");
    expect(figure("Bills held for clients")).toHaveTextContent("Matches the custodian");
    expect(figure("Liquidity buffer")).toHaveTextContent("UGX 142.0m");
    expect(figure("Liquidity buffer")).toHaveTextContent("Target 150.0m, under target");
    expect(figure("Early withdrawals asked")).toHaveTextContent("UGX 23.5m");
    expect(figure("Early withdrawals asked")).toHaveTextContent("4 clients this week");
    expect(positionRow("91 Day Treasury Bill")).toEqual(["7,412.0m", "7,180.0m", "232.0m", "Wed 18 Nov", "On order"]);
    expect(positionRow("USD Fixed Income")).toEqual(["$362,000", "$362,000", "$0", "On demand", "Covered"]);
    expect(screen.getByText("Up to UGX 5.0m each")).toBeInTheDocument();
    expect(screen.getByText("UGX 118.5m")).toBeInTheDocument();
  });

  it("lets a second person approve an order someone else prepared", async () => {
    const { user } = renderDesk();
    const card = await order(BID_91);
    expect(within(card).getByText("UGX 240,000,000")).toBeInTheDocument();
    expect(within(card).getByText("Daniel, 14:20")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Approve as second person" }));
    const approved = await order(BID_91);
    expect(await within(approved).findByText("Approved, waiting for the result")).toBeInTheDocument();
    expect(within(approved).getByRole("button", { name: "Record the result" })).toBeInTheDocument();
    expect(within(approved).queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();
  });

  it("needs a note to send an order back, then takes it off the desk", async () => {
    const { user } = renderDesk();
    const card = await order(BID_182);
    await user.click(within(card).getByRole("button", { name: "Send back" }));
    await user.click(within(card).getByRole("button", { name: "Send back" }));
    expect(within(card).getByText(/Write a short note/)).toBeInTheDocument();
    await user.type(within(card).getByLabelText("Why are you sending it back?"), "Too large for this auction");
    await user.click(within(card).getByRole("button", { name: "Send back" }));
    await order(BID_91);
    expect(screen.queryByRole("article", { name: BID_182 })).not.toBeInTheDocument();
    // The purchase still on order covers 50m of the 154m to buy, so a bid is needed again.
    expect(positionRow("182 Day Treasury Bill")[4]).toBe("Bid needed");
  });

  it("does not let the person who prepared an order approve it", async () => {
    const { user } = renderDesk();
    await order(BID_91);
    await user.click(screen.getByRole("button", { name: "Prepare an order" }));
    const form = screen.getByRole("form", { name: "Prepare an order" });
    await user.selectOptions(within(form).getByLabelText("What is it?"), "Secondary market sale");
    await user.type(within(form).getByLabelText("Face value (UGX)"), "50,000,000");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const mine = await order("Secondary market sale, 91 Day Treasury Bill");
    expect(within(mine).getByText(/You prepared this order, so someone else has to approve it/)).toBeInTheDocument();
    expect(within(mine).queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();
    expect(within(mine).getByRole("button", { name: "Withdraw this order" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Prepare an order" })).not.toBeInTheDocument();
  });

  it("checks a new order before sending it", async () => {
    const { user, api } = renderDesk();
    const prepare = vi.spyOn(api, "prepareOrder");
    await order(BID_91);
    await user.click(screen.getByRole("button", { name: "Prepare an order" }));
    const form = screen.getByRole("form", { name: "Prepare an order" });
    const face = within(form).getByLabelText("Face value (UGX)");
    const send = within(form).getByRole("button", { name: "Send for approval" });

    await user.click(send);
    expect(within(form).getByText("Enter the face value as a whole number.")).toBeInTheDocument();
    expect(face).toBeInvalid();
    await user.type(face, "240,050,000");
    await user.click(send);
    expect(within(form).getByText("Orders are in steps of UGX 100,000.")).toBeInTheDocument();

    await user.selectOptions(within(form).getByLabelText("What is it?"), "Secondary market sale");
    await user.clear(face);
    await user.type(face, "7,400,100,000");
    await user.click(send);
    expect(within(form).getByText(/The desk holds UGX 7,400,000,000 of face value/)).toBeInTheDocument();
    expect(prepare).not.toHaveBeenCalled();
  });

  it("records a result from the custodian's note and leaves it for someone else to confirm", async () => {
    const { user, api } = renderDesk();
    const record = vi.spyOn(api, "recordResult");
    await user.click(within(await order(BID_91)).getByRole("button", { name: "Approve as second person" }));
    await user.click(await within(await order(BID_91)).findByRole("button", { name: "Record the result" }));
    const form = within(await order(BID_91)).getByRole("form", { name: "Record the result" });
    const submit = within(form).getByRole("button", { name: "Record the result" });

    await user.click(submit);
    expect(within(form).getByText("Enter the face value as a whole number.")).toBeInTheDocument();
    expect(within(form).getByText("Enter the maturity date.")).toBeInTheDocument();
    expect(within(form).getByText("Enter the reference on the custodian's note.")).toBeInTheDocument();

    await user.type(within(form).getByLabelText("Face value bought (UGX)"), "240,100,000");
    await user.type(within(form).getByLabelText("Cash paid (UGX)"), "241,000,000");
    await user.click(submit);
    expect(within(form).getByText(/The order was for UGX 240,000,000/)).toBeInTheDocument();
    expect(within(form).getByText(/never costs or fetches more than its face value/)).toBeInTheDocument();
    expect(record).not.toHaveBeenCalled();

    await user.clear(within(form).getByLabelText("Face value bought (UGX)"));
    await user.type(within(form).getByLabelText("Face value bought (UGX)"), "240,000,000");
    await user.clear(within(form).getByLabelText("Cash paid (UGX)"));
    await user.type(within(form).getByLabelText("Cash paid (UGX)"), "232,800,000");
    await user.type(within(form).getByLabelText("Matures on"), "2027-02-12");
    await user.type(within(form).getByLabelText("Custodian reference"), "CSD-2026-0502");
    await user.click(submit);

    const recorded = await order(BID_91);
    expect(await within(recorded).findByText("Result waiting to be confirmed")).toBeInTheDocument();
    expect(within(recorded).getByText("UGX 232,800,000")).toBeInTheDocument();
    expect(within(recorded).getByText(/You recorded this result, so someone else has to confirm it/)).toBeInTheDocument();
    expect(within(recorded).queryByRole("button", { name: /Confirm/ })).not.toBeInTheDocument();
    expect(record).toHaveBeenCalledWith("key-2", "-OdSampleOrder00001A", {
      face: { minor: 240_000_000, currency: "UGX" },
      amount: { minor: 232_800_000, currency: "UGX" },
      settledOn: TODAY,
      maturesOn: "2027-02-12",
      custodianReference: "CSD-2026-0502",
    });
    // Nothing is posted until a second person confirms.
    expect(positionRow("91 Day Treasury Bill")[1]).toBe("7,180.0m");
  });

  it("records an order that won nothing as unsuccessful", async () => {
    const { user } = renderDesk();
    await user.click(within(await order(BID_91)).getByRole("button", { name: "Approve as second person" }));
    await user.click(await within(await order(BID_91)).findByRole("button", { name: "Record the result" }));
    const card = await order(BID_91);
    await user.click(within(card).getByRole("checkbox", { name: "Nothing was dealt" }));
    await user.click(within(card).getByRole("button", { name: "Record as unsuccessful" }));
    await order(BID_182);
    expect(screen.queryByRole("article", { name: BID_91 })).not.toBeInTheDocument();
    expect(positionRow("91 Day Treasury Bill")[4]).toBe("Bid needed");
  });

  it("confirms a result someone else recorded, and the position follows", async () => {
    const { user } = renderDesk();
    const card = await order(PURCHASE_182);
    expect(within(card).getByText("CSD-2026-0481")).toBeInTheDocument();
    expect(within(card).getByText("Grace")).toBeInTheDocument();
    expect(positionRow("182 Day Treasury Bill").slice(1, 3)).toEqual(["9,716.0m", "154.0m"]);
    await user.click(within(card).getByRole("button", { name: "Confirm against the custodian's note" }));
    await order(BID_182);
    expect(screen.queryByRole("article", { name: PURCHASE_182 })).not.toBeInTheDocument();
    expect(positionRow("182 Day Treasury Bill").slice(1, 3)).toEqual(["9,762.9m", "107.1m"]);
    expect(figure("Client money to place")).toHaveTextContent("UGX 339.1m");
    expect(figure("Liquidity buffer")).toHaveTextContent("UGX 142.0m");
  });

  it("sends a wrong result back to be recorded again", async () => {
    const { user } = renderDesk();
    const card = await order(PURCHASE_182);
    await user.click(within(card).getByRole("button", { name: "Reject the result" }));
    await user.type(within(card).getByLabelText("What does not match the custodian's note?"), "Cash paid was 46,950,000");
    await user.click(within(card).getByRole("button", { name: "Reject the result" }));
    const again = await order(PURCHASE_182);
    expect(await within(again).findByText("Approved, waiting for the result")).toBeInTheDocument();
    expect(within(again).getByText("Cash paid was 46,950,000")).toBeInTheDocument();
    expect(within(again).queryByText("CSD-2026-0481")).not.toBeInTheDocument();
  });

  it("says so and reloads when someone else got there first", async () => {
    const api = createStubDealingApi(ME, 0, () => TODAY);
    vi.spyOn(api, "approve").mockRejectedValue(new DealingError("conflict"));
    const reload = vi.spyOn(api, "getDesk");
    const { user } = renderDesk(api);
    await user.click(within(await order(BID_91)).getByRole("button", { name: "Approve as second person" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Someone else has already acted on this order");
    expect(reload).toHaveBeenCalledTimes(2);
    expect(within(await order(BID_182)).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("uses one idempotency key for each attempt", async () => {
    const { user, api } = renderDesk();
    const approve = vi.spyOn(api, "approve");
    await user.click(within(await order(BID_91)).getByRole("button", { name: "Approve as second person" }));
    await user.click(within(await order(BID_182)).getByRole("button", { name: "Approve as second person" }));
    await within(await order(BID_182)).findByText("Approved, waiting for the result");
    expect(approve.mock.calls.map(([key]) => key)).toEqual(["key-1", "key-2"]);
  });

  it("says when the desk is not connected, and offers to try again", async () => {
    renderDesk(notConnectedDealingApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected to the Finvest service yet");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare an order" })).not.toBeInTheDocument();
  });

  it("says when there are no open orders", async () => {
    const api = createStubDealingApi(ME, 0, () => TODAY);
    const desk = await api.getDesk();
    vi.spyOn(api, "getDesk").mockResolvedValue({ ...desk, orders: [] });
    renderDesk(api);
    expect(await screen.findByRole("heading", { name: "No open orders" })).toBeInTheDocument();
  });
});

describe("the sample desk", () => {
  it("applies a command once however often its key is repeated", async () => {
    const api = createStubDealingApi(ME, 0, () => TODAY);
    const order = { kind: "auction_bid", productCode: "TREA-91", face: { minor: 100_000, currency: "UGX" } } as const;
    await api.prepareOrder("same-key", order);
    const again = await api.prepareOrder("same-key", order);
    expect(again.orders).toHaveLength(4);
  });

  it("refuses the person who prepared or recorded something as its second person", async () => {
    const api = createStubDealingApi({ id: "sample-daniel", name: "Daniel" }, 0, () => TODAY);
    await expect(api.approve("k1", "-OdSampleOrder00001A")).rejects.toMatchObject({ code: "same_person" });
    const grace = createStubDealingApi({ id: "sample-grace", name: "Grace" }, 0, () => TODAY);
    await expect(grace.confirmResult("k1", "-OdSampleOrder00003A")).rejects.toMatchObject({ code: "same_person" });
  });
});

describe("dealing desk formatting", () => {
  it("writes exact amounts in full", () => {
    expect(formatExact({ minor: 240_000_000, currency: "UGX" })).toBe("UGX 240,000,000");
    expect(formatExact({ minor: 36_200_050, currency: "USD" })).toBe("$362,000.50");
    expect(formatExact({ minor: -5, currency: "USD" })).toBe("-$0.05");
  });

  it("shortens shillings to millions and dollars to whole dollars", () => {
    expect(formatShort({ minor: 18_240_000_000, currency: "UGX" })).toBe("UGX 18,240.0m");
    expect(formatShort({ minor: 23_500_000, currency: "UGX" }, false)).toBe("23.5m");
    expect(formatShort({ minor: 0, currency: "UGX" }, false)).toBe("0.0m");
    expect(formatShort({ minor: 36_200_000, currency: "USD" })).toBe("$362,000");
  });

  it("reads whole amounts only, with or without thousands separators", () => {
    expect(parseAmount("240,000,000", "UGX")).toEqual({ minor: 240_000_000, currency: "UGX" });
    expect(parseAmount(" 5000 ", "USD")).toEqual({ minor: 500_000, currency: "USD" });
    for (const text of ["", "0", "-5", "1.5", "1,00", "12,0000", "1e6", "abc", "99999999999999999999"]) {
      expect(parseAmount(text, "UGX")).toBeNull();
    }
  });

  it("names an auction day as on the canvas", () => {
    expect(formatAuctionDay("2026-11-18")).toBe("Wed 18 Nov");
  });
});
