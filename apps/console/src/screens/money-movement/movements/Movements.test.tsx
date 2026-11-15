import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { MovementsError, type MovementsApi } from "./api.ts";
import { createHttpMovementsApi } from "./http.ts";
import { Movements } from "./Movements.tsx";
import { createStubMovementsApi } from "./stub.ts";

const NOW = Date.parse("2026-11-15T09:00:00Z");

function renderList(api: MovementsApi = createStubMovementsApi(() => NOW, 0)) {
  render(
    <MemoryRouter>
      <Movements api={api} />
    </MemoryRouter>,
  );
  return userEvent.setup();
}

const amounts = async () => (await screen.findAllByRole("rowheader")).map((cell) => cell.textContent);

describe("every movement", () => {
  it("lists payments in and payouts newest first, a page at a time, and links each client", async () => {
    const user = renderList();
    expect(await amounts()).toEqual(["+UGX 2,500,000", "−UGX 1,000,000", "+UGX 15,000,000", "+UGX 200,000", "−UGX 4,000,000", "+UGX 1,200,000"]);
    const first = screen.getAllByRole("row")[1]!;
    expect(first).toHaveTextContent("Payment in");
    expect(first).toHaveTextContent("MTN Mobile Money");
    expect(first).toHaveTextContent("Received");
    expect(within(first).getByRole("link", { name: "Sarah Nakato" })).toHaveAttribute("href", "/clients?client=-NstubClient00000042");
    await user.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByText("+UGX 750,000")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
  });

  it("narrows to payouts, failures and what is unmatched", async () => {
    const user = renderList();
    await amounts();
    await user.click(screen.getByRole("button", { name: "Payouts" }));
    expect(screen.getByRole("button", { name: "Payouts" })).toHaveAttribute("aria-pressed", "true");
    expect(await amounts()).toEqual(["−UGX 1,000,000", "−UGX 4,000,000", "−UGX 300,000"]);
    await user.click(screen.getByRole("button", { name: "Failed" }));
    expect(await amounts()).toEqual(["+UGX 200,000", "−UGX 300,000"]);
    expect(screen.getByText("account not found")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Unmatched" }));
    expect(await amounts()).toEqual(["+UGX 15,000,000", "+UGX 1,200,000"]);
    expect(screen.getByText("Not found on statement yet")).toBeInTheDocument();
    expect(screen.getByText("Outcome not known")).toBeInTheDocument();
  });

  it("says when there is nothing, and when the list cannot be loaded", async () => {
    const api = createStubMovementsApi(() => NOW, 0, "empty");
    const user = renderList(api);
    expect(await screen.findByText("No money has moved yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Unmatched" }));
    expect(await screen.findByText(/Everything is matched/)).toBeInTheDocument();
  });

  it("offers to try again after a failure", async () => {
    const list = vi.fn<MovementsApi["list"]>().mockRejectedValueOnce(new MovementsError("unavailable")).mockResolvedValue({ movements: [], nextAfter: null });
    const user = renderList({ list });
    expect(await screen.findByText("Movements could not be loaded")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No money has moved yet.")).toBeInTheDocument();
  });
});

describe("the movements route", () => {
  it("asks with the filter and page place and reads the backend's shape", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            movements: [
              {
                kind: "payout",
                id: "-NpayoutA00000000001",
                client_id: "-Nclient00000000000A",
                client_name: null,
                channel: "bank",
                amount: { minor: 1_000_000, currency: "UGX" },
                status: "failed",
                failure_code: "account_closed",
                created_at: "2026-11-15T08:00:00Z",
                ended_at: "2026-11-15T08:30:00Z",
              },
            ],
            next_after: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const api = createHttpMovementsApi(createHttp("https://api.test", fetch));
    const page = await api.list("failed", "2026-11-15T09:00:00+00:00 -NpayoutA00000000002");
    const url = new URL(String(fetch.mock.calls[0]?.[0]));
    expect(url.pathname).toBe("/staff/money-movements");
    expect(Object.fromEntries(url.searchParams)).toEqual({ show: "failed", after: "2026-11-15T09:00:00+00:00 -NpayoutA00000000002" });
    expect(page.movements[0]).toEqual({
      kind: "payout",
      id: "-NpayoutA00000000001",
      clientId: "-Nclient00000000000A",
      clientName: null,
      channel: "bank",
      amount: { minor: 1_000_000, currency: "UGX" },
      status: "failed",
      failureCode: "account_closed",
      createdAt: "2026-11-15T08:00:00Z",
      endedAt: "2026-11-15T08:30:00Z",
    });
    await api.list("all", null);
    expect(String(fetch.mock.calls[1]?.[0])).toBe("https://api.test/staff/money-movements");
  });
});
