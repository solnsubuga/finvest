import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { CatalogueError, notConnectedCatalogueApi, type CatalogueApi } from "./api.ts";
import { amountInput, formatBps, formatExact, parseAmount, parsePercent, previewClientRate } from "./format.ts";
import { ProductsScreen } from "./index.tsx";
import { createStubCatalogueApi } from "./stub.ts";

const TODAY = "2026-11-13";

function renderProducts(
  api: CatalogueApi = createStubCatalogueApi({ me: "me", today: TODAY }),
  { at = "/products", canChange = false }: { at?: string; canChange?: boolean } = {},
) {
  render(
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        <Route path="/products" element={<ProductsScreen api={api} me="me" canChange={canChange} />} />
        <Route path="/rates" element={<p>Rates</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return userEvent.setup();
}

function row(product: string): string[] {
  const header = screen.getByRole("rowheader", { name: new RegExp(`^${product}`) });
  return within(header.closest("tr") as HTMLElement)
    .getAllByRole("cell")
    .map((cell) => cell.textContent ?? "");
}

describe("products screen", () => {
  it("shows each product's rate before tax, the tax, and what clients see", async () => {
    renderProducts();
    await screen.findByRole("table", { name: "Products" });
    expect(row("91 Day Treasury Bill")).toEqual(["12.5%", "15%", "10.63%", "UGX 100,000", "Live"]);
    expect(row("182 Day Treasury Bill")).toEqual(["13.75%", "15%", "11.69%", "UGX 200,000", "Live"]);
    expect(row("USD Fixed Income")).toEqual(["7.25%", "5%", "6.89%", "$500.00", "Paused"]);
    expect(screen.getByRole("link", { name: "Change a rate" })).toHaveAttribute("href", "/rates");
  });

  it("shows a product as a client sees it, without the tax", async () => {
    const user = renderProducts();
    const panel = await screen.findByRole("region", { name: "91 Day Treasury Bill" });
    expect(within(panel).getByText("10.63% a year")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "USD Fixed Income" }));
    const usd = screen.getByRole("region", { name: "USD Fixed Income" });
    expect(within(usd).getByText("6.89% a year")).toBeInTheDocument();
    expect(within(usd).getByText("Open term")).toBeInTheDocument();
    expect(within(usd).getByText(/clients can see it but not invest/)).toBeInTheDocument();
  });

  it("shows an approved rate that starts later", async () => {
    const api = createStubCatalogueApi({ me: "me", today: TODAY });
    const [waiting] = await api.listProposals("proposed");
    await api.approve(waiting?.id ?? "");
    renderProducts(api);
    await screen.findByRole("table", { name: "Products" });
    expect(row("91 Day Treasury Bill")[2]).toBe("10.63%11.05% from 1 Dec 2026");
  });

  it("offers to try again when products cannot be loaded", async () => {
    let calls = 0;
    const flaky: CatalogueApi = {
      ...notConnectedCatalogueApi,
      listProducts: () => (++calls === 1 ? Promise.reject(new CatalogueError("unavailable")) : createStubCatalogueApi({ me: "me", today: TODAY }).listProducts()),
      listProductChanges: () => createStubCatalogueApi({ me: "me", today: TODAY }).listProductChanges("proposed"),
    };
    const user = renderProducts(flaky);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("We could not reach Finvest");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: "Products" })).toBeInTheDocument();
  });

  it("says when it is not connected", async () => {
    renderProducts(notConnectedCatalogueApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected to the Finvest service yet");
  });
});

describe("changing products", () => {
  it("hides every change from staff without products.change", async () => {
    renderProducts();
    await screen.findByRole("table", { name: "Products" });
    expect(screen.queryByRole("link", { name: "New product" })).toBeNull();
    expect(screen.queryByRole("link", { name: /^Edit / })).toBeNull();
    const waiting = screen.getByRole("region", { name: "364 Day Treasury Bill" });
    expect(within(waiting).queryByRole("button", { name: "Approve as second person" })).toBeNull();
  });

  it("lets a second person approve a colleague's new product, which starts as a draft", async () => {
    const user = renderProducts(undefined, { canChange: true });
    const waiting = await screen.findByRole("region", { name: "364 Day Treasury Bill" });
    expect(within(waiting).getByText("New product · waiting for you")).toBeInTheDocument();
    expect(within(waiting).getByText("12.75% a year")).toBeInTheDocument();
    await user.click(within(waiting).getByRole("button", { name: "Approve as second person" }));
    expect(await screen.findByRole("rowheader", { name: /^364 Day Treasury Bill/ })).toBeInTheDocument();
    expect(row("364 Day Treasury Bill")).toEqual(["15%", "15%", "12.75%", "UGX 500,000", "Draft"]);
    expect(screen.queryByText("Waiting for approval")).toBeNull();
  });

  it("sends a new product for approval and shows it waiting for a second person", async () => {
    const api = createStubCatalogueApi({ me: "me", today: TODAY, sample: "empty" });
    const user = renderProducts(api, { at: "/products?new", canChange: true });
    await screen.findByRole("heading", { name: "New product", level: 1 });
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(screen.getByText("Use capital letters, digits and hyphens, such as TREA-364.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Code"), "trea-273");
    await user.type(screen.getByLabelText("Name"), "273 Day Treasury Bill");
    await user.type(screen.getByLabelText("Term, days"), "273");
    await user.type(screen.getByLabelText("Minimum, UGX"), "300,000");
    await user.type(screen.getByLabelText("Yearly rate before tax, %"), "14.5");
    expect(screen.getByText("12.33% a year, after tax")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Reason, for the audit trail"), "Clients ask for nine months");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    const waiting = await screen.findByRole("region", { name: "273 Day Treasury Bill" });
    expect(within(waiting).getByText("New product · waiting for a second person")).toBeInTheDocument();
    expect(within(waiting).getByText("TREA-273")).toBeInTheDocument();
    expect((await api.listProductChanges("proposed"))[0]?.minimum).toEqual({ minor: 300_000, currency: "UGX" });
  });

  it("refuses a code that is already taken", async () => {
    const user = renderProducts(undefined, { at: "/products?new", canChange: true });
    await screen.findByRole("heading", { name: "New product", level: 1 });
    await user.type(screen.getByLabelText("Code"), "TREA-91");
    await user.type(screen.getByLabelText("Name"), "Again");
    await user.type(screen.getByLabelText("Term, days"), "91");
    await user.type(screen.getByLabelText("Minimum, UGX"), "100000");
    await user.type(screen.getByLabelText("Yearly rate before tax, %"), "12");
    await user.type(screen.getByLabelText("Reason, for the audit trail"), "Test");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A product with this code already exists");
  });

  it("keeps the kind of a live product and sends new terms for approval", async () => {
    const user = renderProducts(undefined, { at: "/products?edit=TREA-91", canChange: true });
    await screen.findByRole("heading", { name: "Edit 91 Day Treasury Bill" });
    expect(screen.getByLabelText("Kind")).toBeDisabled();
    expect(screen.getByLabelText("Interest is paid")).toBeDisabled();
    expect(screen.getByText("12.5% before tax, 15% tax. Clients see 10.63%.")).toBeInTheDocument();
    const reach = screen.getByRole("complementary", { name: "Who this reaches and availability" });
    expect(await within(reach).findByText("912 clients keep their terms")).toBeInTheDocument();
    const minimum = screen.getByLabelText("Minimum, UGX");
    await user.clear(minimum);
    await user.type(minimum, "50000");
    await user.type(screen.getAllByLabelText("Reason, for the audit trail")[0] as HTMLElement, "Let more people start");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    const waiting = await screen.findByRole("region", { name: "91 Day Treasury Bill" });
    expect(within(waiting).getByText("UGX 100,000 to UGX 50,000")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send for approval" })).toBeDisabled();
  });

  it("pauses a live product with a reason", async () => {
    const api = createStubCatalogueApi({ me: "me", today: TODAY });
    const user = renderProducts(api, { at: "/products?edit=TREA-182", canChange: true });
    await screen.findByRole("heading", { name: "Pause new investments" });
    await user.click(screen.getByRole("button", { name: "Send pause for approval" }));
    expect(screen.getByText("Say why.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Reason, for the audit trail", { selector: "#status-reason" }), "Auction cancelled");
    await user.click(screen.getByRole("button", { name: "Send pause for approval" }));
    const waiting = await screen.findByRole("region", { name: "182 Day Treasury Bill" });
    expect(within(waiting).getByText("Live to Paused")).toBeInTheDocument();
    expect((await api.listProductChanges("proposed")).map((change) => change.action)).toContain("pause");
  });

  it("does not offer to open the dollar product until dollars can be paid in and out", async () => {
    renderProducts(undefined, { at: "/products?edit=USD-BOND", canChange: true });
    await screen.findByRole("heading", { name: "Open again" });
    expect(screen.getByText(/cannot take payments or pay out in dollars yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send opening for approval" })).toBeDisabled();
  });
});

describe("rate formats", () => {
  it("writes hundredths of a percent without trailing zeros", () => {
    expect(formatBps(1250)).toBe("12.5%");
    expect(formatBps(1375)).toBe("13.75%");
    expect(formatBps(1500)).toBe("15%");
    expect(formatBps(5)).toBe("0.05%");
  });

  it("reads a typed percentage without floating point", () => {
    expect(parsePercent("12.5")).toBe(1250);
    expect(parsePercent("12.50%")).toBe(1250);
    expect(parsePercent("0.07")).toBe(7);
    expect(parsePercent("15")).toBe(1500);
    expect(parsePercent("12.555")).toBeNull();
    expect(parsePercent("abc")).toBeNull();
    expect(parsePercent("")).toBeNull();
  });

  it("previews the client rate rounded as the backend rounds it", () => {
    expect(previewClientRate(1250, 1500)).toBe("10.63%");
    expect(previewClientRate(1375, 1500)).toBe("11.69%");
    expect(previewClientRate(1300, 1500)).toBe("11.05%");
    expect(previewClientRate(725, 500)).toBe("6.89%");
  });

  it("reads a typed amount in whole units without floating point", () => {
    expect(parseAmount("100,000", "UGX")).toBe(100_000);
    expect(parseAmount("100000.5", "UGX")).toBeNull();
    expect(parseAmount("500.00", "USD")).toBe(500_00);
    expect(parseAmount("500.5", "USD")).toBe(500_50);
    expect(parseAmount("0", "UGX")).toBeNull();
    expect(parseAmount("-1", "UGX")).toBeNull();
    expect(amountInput({ minor: 500_00, currency: "USD" })).toBe("500.00");
  });

  it("writes exact amounts", () => {
    expect(formatExact({ minor: 100_000, currency: "UGX" })).toBe("UGX 100,000");
    expect(formatExact({ minor: 500_00, currency: "USD" })).toBe("$500.00");
  });
});
