import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { CatalogueError, notConnectedCatalogueApi, type CatalogueApi } from "./api.ts";
import { formatBps, formatExact, parsePercent, previewClientRate } from "./format.ts";
import { ProductsScreen } from "./index.tsx";
import { createStubCatalogueApi } from "./stub.ts";

const TODAY = "2026-11-13";

function renderProducts(api: CatalogueApi = createStubCatalogueApi({ me: "me", today: TODAY })) {
  render(
    <MemoryRouter>
      <ProductsScreen api={api} />
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

  it("writes exact amounts", () => {
    expect(formatExact({ minor: 100_000, currency: "UGX" })).toBe("UGX 100,000");
    expect(formatExact({ minor: 500_00, currency: "USD" })).toBe("$500.00");
  });
});
