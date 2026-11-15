import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/http.ts";
import { createStubCatalogueApi } from "../stub.ts";
import { ProductsScreen } from "../index.tsx";
import { BondsError, clientCoupon, formatPrice, parsePrice, usualTaxBps, type BondsApi } from "./api.ts";
import { toBondsError } from "./http.ts";
import { createStubBondsApi } from "./stub.ts";

function renderBonds(bondsApi: BondsApi = createStubBondsApi({ me: "me" }), { at = "/products", canChange = false }: { at?: string; canChange?: boolean } = {}) {
  render(
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        <Route path="/products" element={<ProductsScreen api={createStubCatalogueApi({ me: "me", today: "2026-11-15" })} bondsApi={bondsApi} me="me" canChange={canChange} />} />
      </Routes>
    </MemoryRouter>,
  );
  return userEvent.setup();
}

function row(bond: string): string[] {
  const header = screen.getByRole("rowheader", { name: new RegExp(`^${bond}`) });
  return within(header.closest("tr") as HTMLElement)
    .getAllByRole("cell")
    .map((cell) => cell.textContent ?? "");
}

describe("bond figures", () => {
  it("reads and shows prices to four places, never as a float", () => {
    expect(parsePrice("98.5")).toBe(985_000);
    expect(parsePrice("101.125")).toBe(1_011_250);
    expect(parsePrice("100")).toBe(1_000_000);
    expect(parsePrice("49.9999")).toBeNull();
    expect(parsePrice("98.12345")).toBeNull();
    expect(parsePrice("abc")).toBeNull();
    expect(formatPrice(985_000)).toBe("98.5000");
  });

  it("works out the usual tax and the coupon clients see, as the backend does", () => {
    expect(usualTaxBps("2026-10-28", "2032-05-20")).toBe(2000);
    expect(usualTaxBps("2027-05-18", "2037-05-14")).toBe(1000);
    expect(clientCoupon(1600, 1000)).toBe("14.40%");
    expect(clientCoupon(1525, 2000)).toBe("12.20%");
  });

  it("maps the backend's refusals", () => {
    const refused = new ApiError("validation_failed", 422, [{ field_path: ["isin"], code: "isin_taken" }]);
    expect(toBondsError(refused).code).toBe("isin_taken");
    expect(toBondsError(new ApiError("confirm_it_is_you", 403)).code).toBe("not_confirmed");
    expect(toBondsError(new Error("boom")).code).toBe("unavailable");
  });
});

describe("treasury bonds on the products screen", () => {
  it("lists each bond with its coupon, tax, what clients see, maturity and price", async () => {
    renderBonds();
    await screen.findByRole("table", { name: "Bonds" });
    expect(row("2 Year Treasury Bond 2028")).toEqual(["15.25%", "20%", "12.20%", "16 Nov 2028", "98.7120", "Live"]);
    expect(row("25 Year Treasury Bond 2050")).toEqual(["16%", "10%", "14.40%", "7 Jul 2050", "87.5600", "Paused"]);
    expect(screen.queryByRole("link", { name: "New bond" })).not.toBeInTheDocument();
  });

  it("shows an empty catalogue and an unreachable Finvest", async () => {
    renderBonds(createStubBondsApi({ me: "me", sample: "empty" }), { canChange: true });
    expect(await screen.findByRole("heading", { name: "No bonds yet" })).toBeInTheDocument();
    // Bonds are added through New product, like every other product.
    expect(screen.queryByRole("link", { name: "New bond" })).not.toBeInTheDocument();
  });

  it("says when bonds cannot be loaded", async () => {
    renderBonds(createStubBondsApi({ me: "me", sample: "offline" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Bonds could not be loaded")).toBeInTheDocument();
  });

  it("lets a second person approve a colleague's new price", async () => {
    const api = createStubBondsApi({ me: "me" });
    const user = renderBonds(api, { canChange: true });
    const card = await screen.findByRole("region", { name: "New price: BOND-2Y-2028" });
    expect(within(card).getByText("98.9450")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Approve as second person" }));
    await screen.findByRole("table", { name: "Bonds" });
    expect(row("2 Year Treasury Bond 2028")[4]).toBe("98.9450");
    expect(screen.queryByRole("region", { name: "New price: BOND-2Y-2028" })).not.toBeInTheDocument();
  });

  it("proposes a new price with a reason", async () => {
    const api = createStubBondsApi({ me: "me" });
    const user = renderBonds(api, { canChange: true });
    await user.click(await screen.findByRole("button", { name: "5 Year Treasury Bond 2032" }));
    const price = screen.getByLabelText("Today's price per 100 of face value");
    await user.clear(price);
    await user.type(price, "96");
    await user.click(screen.getByRole("button", { name: "Propose new price" }));
    expect(screen.getByText("Say why. The reason goes in the audit trail.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Reason"), "Auction cut-off yield rose");
    await user.click(screen.getByRole("button", { name: "Propose new price" }));
    const card = await screen.findByRole("region", { name: "New price: BOND-5Y-2032" });
    expect(within(card).getByRole("button", { name: "Withdraw this change" })).toBeInTheDocument();
  });

  it("fills the tax in from the dates and shows what clients will see", async () => {
    const api = createStubBondsApi({ me: "me", sample: "empty" });
    const user = renderBonds(api, { at: "/products?new", canChange: true });
    await user.selectOptions(await screen.findByLabelText("Type"), "Treasury bond");
    await user.type(await screen.findByLabelText("ISIN"), "UG12K1405378");
    await user.type(screen.getByLabelText("Code"), "BOND-10Y-2037");
    await user.type(screen.getByLabelText("Name"), "10 Year Treasury Bond 2037");
    await user.type(screen.getByLabelText("Issue date"), "2027-05-18");
    await user.type(screen.getByLabelText("Maturity date"), "2037-05-14");
    expect(screen.getByLabelText("Tax on coupons, %")).toHaveValue("10");
    await user.type(screen.getByLabelText("Coupon, % a year before tax"), "16");
    expect(screen.getByText("14.40% a year")).toBeInTheDocument();
    await user.type(screen.getByLabelText("First price per 100 of face value"), "98.5");
    await user.type(screen.getByLabelText("Reason"), "From the auction result");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    const [change] = await api.listChanges("proposed");
    expect(change).toMatchObject({ action: "create", code: "BOND-10Y-2037", price: 985_000, terms: { couponBps: 1600, taxBps: 1000, isin: "UG12K1405378" } });
  });

  it("refuses a new bond that is not filled in", async () => {
    const user = renderBonds(createStubBondsApi({ me: "me" }), { at: "/products?new-bond", canChange: true });
    await user.click(await screen.findByRole("button", { name: "Send for approval" }));
    expect(screen.getByText(/Twelve characters, as on the auction result/)).toBeInTheDocument();
    expect(screen.getByText(/Type both dates/)).toBeInTheDocument();
  });

  it("names a refusal from Finvest", () => {
    expect(new BondsError("same_price").code).toBe("same_price");
  });
});
