import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { STUB_ROLE_PERMISSIONS } from "@/auth/stub.ts";
import type { Role } from "@/auth/types.ts";
import { createHttpFiguresApi, createStubFiguresApi, type FiguresApi } from "./figures.ts";
import { exact } from "./Figures.tsx";
import { OverviewScreen } from "./index.tsx";
import { createStubApprovalsApi } from "./stub.ts";

const NOW = Date.parse("2026-11-13T09:00:00Z");

function renderOverview(figures: FiguresApi, role: Role = "admin") {
  const approvals = createStubApprovalsApi({ id: "stub-me", name: "Solomon N.", permissions: STUB_ROLE_PERMISSIONS[role] }, () => NOW);
  render(
    <MemoryRouter>
      <OverviewScreen api={approvals} figures={figures} now={() => new Date(NOW)} />
    </MemoryRouter>,
  );
  return userEvent.setup();
}

const figuresFor = (role: Role, sample: "sample" | "empty" | "error" = "sample") =>
  createStubFiguresApi(STUB_ROLE_PERMISSIONS[role], () => NOW, sample);

describe("overview figures", () => {
  it("shows the money tiles, the week, floats, balancing, maturities and holdings to an admin", async () => {
    renderOverview(figuresFor("admin"));
    expect((await screen.findByText("Client money held")).nextSibling).toHaveTextContent("UGX 4,820,350,000");
    expect(screen.getByRole("list", { name: "Money in and out by day" }).children).toHaveLength(7);
    const floats = screen.getByRole("list", { name: "Mobile money floats" });
    expect(floats).toHaveTextContent("Below the UGX 10,000,000 warning line");
    const attention = screen.getByRole("list", { name: "Needs attention" });
    expect(within(attention).getAllByRole("listitem")[0]).toHaveTextContent("MTN payout float is low");
    expect(within(attention).getByRole("link", { name: /items in the review queue/ })).toHaveAttribute("href", "/review-queue");
    expect(screen.getByRole("list", { name: "Maturities by week" }).children).toHaveLength(4);
    expect(screen.getByRole("list", { name: "Where client money sits" })).toHaveTextContent("91 Day Treasury Bill");
    expect(screen.getByText("Last day balanced")).toBeInTheDocument();
  });

  it("leaves out what a role may not see", async () => {
    renderOverview(figuresFor("support"), "support");
    expect(await screen.findByRole("heading", { name: "Needs attention" })).toBeInTheDocument();
    expect(screen.queryByText("Client money held")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Mobile money floats" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Cash needed for maturities" })).not.toBeInTheDocument();
  });

  it("switches to dollars, with no floats", async () => {
    const user = renderOverview(figuresFor("admin"));
    await screen.findByText("Client money held");
    await user.click(screen.getByRole("button", { name: "Dollars" }));
    expect((await screen.findByText("Client money held")).nextSibling).toHaveTextContent(/^\$[\d,]+\.00$/);
    expect(screen.queryByRole("heading", { name: "Mobile money floats" })).not.toBeInTheDocument();
  });

  it("says when nothing needs attention, and when the figures cannot be loaded", async () => {
    renderOverview(figuresFor("admin", "empty"));
    expect(await screen.findByText(/Nothing needs attention/)).toBeInTheDocument();
    expect(screen.getByText("No client money is invested in this currency yet.")).toBeInTheDocument();
    renderOverview(figuresFor("admin", "error"));
    expect(await screen.findByRole("heading", { name: "The figures could not be loaded" })).toBeInTheDocument();
  });

  it("writes amounts exactly from minor units", () => {
    expect(exact({ minor: 1_234_567, currency: "UGX" })).toBe("UGX 1,234,567");
    expect(exact({ minor: 1_234_567, currency: "USD" })).toBe("$12,345.67");
    expect(exact({ minor: -5, currency: "USD" })).toBe("-$0.05");
  });

  it("reads the figures from the API in the currency asked for", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            today: "2026-11-13",
            currency: "USD",
            money: null,
            floats: null,
            balancing: { last_closed: "2026-11-11", waiting_for_approval: null, days_behind: 1 },
            maturities: null,
            holdings: null,
            attention: [{ code: "books_not_balanced", tone: "warning", count: 1, since: null, subject: "2026-11-11" }],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const api = createHttpFiguresApi(createHttp("https://api.test", fetch));
    const figures = await api.load("USD");
    expect(String(fetch.mock.calls[0]?.[0])).toBe("https://api.test/staff/overview?currency=USD");
    expect(figures.balancing).toEqual({ lastClosed: "2026-11-11", waitingForApproval: null, daysBehind: 1 });
    expect(figures.attention[0]?.code).toBe("books_not_balanced");
  });
});
