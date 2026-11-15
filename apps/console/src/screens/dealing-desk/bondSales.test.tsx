import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import type { Directory } from "@/directory/api.ts";
import type { StaffBondSaleOut } from "@finvest/api-client/staff";
import { BondSalesSection } from "./BondSales.tsx";
import { BondSaleError, notConnectedBondSalesApi, type BondSalesApi } from "./bondSales.ts";
import { createHttpBondSalesApi, toBondSaleError } from "./bondSalesHttp.ts";
import { createStubBondSalesApi } from "./bondSalesStub.ts";

const TODAY = "2027-02-14";
const ME = { id: "-StaffMe000000000001", name: "Ada Namubiru" };

function renderSales(api: BondSalesApi = createStubBondSalesApi(ME, "sample", () => TODAY)) {
  render(<BondSalesSection api={api} today={() => TODAY} />);
  return userEvent.setup();
}

const card = (name: string) => screen.findByRole("article", { name: new RegExp(`^${name},`) });

describe("bonds clients are selling", () => {
  it("lists the sales waiting for the desk, with the steps each reader may take", async () => {
    renderSales();
    const waiting = await screen.findByRole("list", { name: "Sales waiting, newest first" });
    expect(within(waiting).getAllByRole("article")).toHaveLength(3);
    const asked = await card("Sarah Nakato");
    expect(within(asked).getByText("Waiting for the desk to sell")).toBeInTheDocument();
    expect(within(asked).getByRole("button", { name: /Record price for/ })).toBeInTheDocument();
    const colleague = await card("Brian Okello");
    expect(within(colleague).getByText("97.2500 per 100")).toBeInTheDocument();
    expect(within(colleague).getByRole("button", { name: /^Approve/ })).toBeInTheDocument();
    expect(within(colleague).getByRole("button", { name: /^Send back/ })).toBeInTheDocument();
    const mine = await card("Joan Achieng");
    expect(within(mine).queryByRole("button", { name: /^Approve/ })).toBeNull();
    expect(within(mine).getByRole("button", { name: /^Withdraw price for/ })).toBeInTheDocument();
  });

  it("records a price after checking what was typed", async () => {
    const user = renderSales();
    const asked = await card("Sarah Nakato");
    await user.click(within(asked).getByRole("button", { name: /Record price for/ }));
    const form = within(asked).getByRole("form", { name: "Record the price" });
    await user.click(within(form).getByRole("button", { name: "Record price" }));
    expect(within(form).getByText(/between 50 and 150/)).toBeInTheDocument();
    expect(within(form).getByText("Enter the desk's reference for the trade.")).toBeInTheDocument();

    await user.type(within(form).getByLabelText("Price per 100"), "97.25");
    await user.type(within(form).getByLabelText("Desk reference"), "Desk trade 4480");
    await user.click(within(form).getByRole("button", { name: "Record price" }));
    const priced = await card("Sarah Nakato");
    expect(await within(priced).findByText("Waiting for a second person")).toBeInTheDocument();
    expect(within(priced).getByText("You recorded this price. A different person approves it.")).toBeInTheDocument();
  });

  it("approves a colleague's price", async () => {
    const user = renderSales();
    const colleague = await card("Brian Okello");
    await user.click(within(colleague).getByRole("button", { name: /^Approve/ }));
    expect(await within(await card("Brian Okello")).findByText("Approved, being paid")).toBeInTheDocument();
  });

  it("sends a sale back with a note, and it waits to be priced again", async () => {
    const user = renderSales();
    const colleague = await card("Brian Okello");
    await user.click(within(colleague).getByRole("button", { name: /^Send back/ }));
    const form = within(colleague).getByRole("form", { name: "Send back" });
    await user.click(within(form).getByRole("button", { name: "Send back" }));
    expect(within(form).getByText("Say what is wrong, so the desk can put it right.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText("Why it goes back"), "That is the bid, not the trade");
    await user.click(within(form).getByRole("button", { name: "Send back" }));
    const back = await card("Brian Okello");
    expect(await within(back).findByText("Waiting for the desk to sell")).toBeInTheDocument();
    expect(within(back).getByText("That is the bid, not the trade")).toBeInTheDocument();
  });

  it("shows what a sold bond paid the client", async () => {
    const user = renderSales();
    await user.click(await screen.findByText("Sold, cancelled and closed (1)"));
    const done = screen.getByRole("list", { name: "Sales done, newest first" });
    expect(within(done).getByText("UGX 1,009,096")).toBeInTheDocument();
    expect(within(done).getByText("UGX 4,066")).toBeInTheDocument();
    expect(within(done).getByText("Sent to the client's payout account")).toBeInTheDocument();
  });

  it("says when nobody is selling, and when the list cannot be loaded", async () => {
    renderSales(createStubBondSalesApi(ME, "empty", () => TODAY));
    expect(await screen.findByText(/No client is waiting to sell a bond/)).toBeInTheDocument();
  });

  it("offers to try again when Finvest cannot be reached", async () => {
    renderSales(notConnectedBondSalesApi);
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Bond sales could not be loaded")).toBeInTheDocument();
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("reloads the list when someone else moved the sale on", async () => {
    const api = createStubBondSalesApi(ME, "sample", () => TODAY);
    const approve = vi.spyOn(api, "approve").mockRejectedValueOnce(new BondSaleError("conflict"));
    const user = renderSales(api);
    const colleague = await card("Brian Okello");
    await user.click(within(colleague).getByRole("button", { name: /^Approve/ }));
    expect(await within(await card("Brian Okello")).findByText(/Someone else has already moved this sale on/)).toBeInTheDocument();
    expect(approve).toHaveBeenCalledOnce();
  });
});

describe("bond sales over the API", () => {
  const row: StaffBondSaleOut = {
    id: "-Sale0000000000000001",
    holding_id: "-Holding00000000001",
    client_id: "-Client000000000001",
    code: "BOND-10Y-2037",
    name: "10 Year Treasury Bond 2037",
    currency: "UGX",
    face_minor: 1_000_000,
    cost_minor: 985_883,
    destination_id: "-Destination0000001",
    requested_at: "2027-02-14T07:00:00Z",
    status: "priced",
    price: 972_500,
    sold_on: "2027-02-14",
    reason: "Desk trade",
    priced_by: "-Staff000000000001",
    priced_at: "2027-02-14T08:00:00Z",
    decided_by: null,
    decided_at: null,
    note: null,
    clean_minor: null,
    accrued_minor: null,
    tax_minor: null,
    amount_minor: null,
    sold_at: null,
    payout_status: null,
    payout_id: null,
    held_because: null,
    closed_at: null,
    allowed: ["approve", "send_back"],
  };
  const directory: Directory = {
    client: (id) => Promise.resolve({ clientId: id, name: "Sarah Nakato", accountNumber: null, verified: true, clientSince: "2026-01-01", status: "active" }),
    findClients: () => Promise.resolve([]),
    staffName: () => Promise.resolve("Grace Atim"),
    team: () => Promise.resolve([]),
  };

  it("reads the sales with the client's and staff's names, and sends each step", async () => {
    const request = vi.fn((method: string) => Promise.resolve(method === "GET" ? [row] : row));
    const api = createHttpBondSalesApi({ request } as unknown as Http, directory);
    const [sale] = await api.list();
    expect(sale).toMatchObject({ clientName: "Sarah Nakato", pricedBy: "Grace Atim", price: 972_500, allowed: ["approve", "send_back"] });
    await api.price(row.id, { price: 972_500, soldOn: "2027-02-14", reason: "Desk trade" });
    expect(request).toHaveBeenLastCalledWith("POST", `/staff/bond-sales/${row.id}/price`, {
      price: 972_500,
      sold_on: "2027-02-14",
      reason: "Desk trade",
    });
    await api.sendBack(row.id, "Wrong");
    expect(request).toHaveBeenLastCalledWith("POST", `/staff/bond-sales/${row.id}/send-back`, { note: "Wrong" });
  });

  it("names a client the directory does not know as a client", async () => {
    const request = vi.fn(() => Promise.resolve([row]));
    const unknown: Directory = { ...directory, client: () => Promise.resolve(null) };
    const [sale] = await createHttpBondSalesApi({ request } as unknown as Http, unknown).list();
    expect(sale?.clientName).toBe("A client");
  });

  it("turns the backend's refusals into the screen's", () => {
    const field = new ApiError("validation_failed", 422, [{ field_path: ["sold_on"], code: "coupon_since_sale" }]);
    expect(toBondSaleError(field).code).toBe("coupon_since_sale");
    expect(toBondSaleError(new ApiError("conflict", 409)).code).toBe("conflict");
    expect(toBondSaleError(new Error("offline")).code).toBe("unavailable");
  });
});
