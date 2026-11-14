import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { notConnectedTaxApi } from "./api.ts";
import { FinanceScreen } from "./index.tsx";
import { monthCloseErrorMessage, notConnectedMonthCloseApi, refusalText, type MonthCloseApi } from "./monthClose.ts";
import { createHttpMonthCloseApi } from "./monthCloseHttp.ts";
import { createStubMonthCloseApi, type MonthCloseStubOptions } from "./monthCloseStub.ts";

const ADMIN = { id: "stub-admin", name: "Solomon N." };
const FINANCE = { id: "stub-finance", name: "Daniel Opio" };
const NOW = () => Date.parse("2026-11-14T09:00:00Z");

function renderClose({ me = ADMIN, options = {}, api }: { me?: typeof ADMIN; options?: MonthCloseStubOptions; api?: MonthCloseApi } = {}) {
  let keys = 0;
  render(
    <FinanceScreen
      api={notConnectedTaxApi}
      canPay={false}
      today={() => "2026-11-14"}
      newKey={() => `key-${++keys}`}
      monthClose={{ api: api ?? createStubMonthCloseApi(me, { now: NOW, ...options }), me: me.id }}
    />,
  );
  return userEvent.setup();
}

const section = () => screen.getByRole("region", { name: "Close the month" });
const waiting = () => within(section()).findByRole("group", { name: "October 2026, waiting for approval" });

describe("closing the month", () => {
  it("shows a colleague's waiting close with its books, for a second person to approve", async () => {
    const user = renderClose();
    const close = await waiting();
    expect(within(section()).getByText("Last month closed").nextSibling).toHaveTextContent("September 2026");
    expect(within(close).getByText(/Proposed by Daniel Opio/)).toBeInTheDocument();
    const ugx = within(close).getByRole("table", { name: "Books in UGX, as proposed" });
    const trust = within(ugx).getByRole("row", { name: /bank\.trust/ });
    expect(trust).toHaveTextContent("1,204,500,000");
    expect(trust).toHaveTextContent("1,248,850,000");
    expect(within(close).getByRole("table", { name: "Books in USD, as proposed" })).toHaveTextContent("52,050.00");
    await user.click(within(close).getByRole("button", { name: "Approve and close" }));
    const form = within(close).getByRole("form", { name: "Approve and close, October 2026" });
    expect(within(form).getByRole("button", { name: "Approve and close" })).toBeDisabled();
    await user.type(within(form).getByLabelText("Reason"), "Checked against the bank");
    await user.click(within(form).getByRole("button", { name: "Approve and close" }));
    expect(await within(section()).findByText("October 2026 is closed. Its books are kept as signed.")).toBeInTheDocument();
    // November has not ended, so nothing can be proposed next.
    expect(await within(section()).findByText("November 2026 has not ended yet.")).toBeInTheDocument();
    expect(within(section()).getByRole("list", { name: "Earlier closes" })).toHaveTextContent("you approved");
  });

  it("lets the proposer withdraw, never approve", async () => {
    const user = renderClose({ me: FINANCE });
    const close = await waiting();
    expect(within(close).queryByRole("button", { name: "Approve and close" })).not.toBeInTheDocument();
    await user.click(within(close).getByRole("button", { name: "Withdraw" }));
    await user.type(within(close).getByLabelText("Reason"), "A late bank charge");
    await user.click(within(within(close).getByRole("form")).getByRole("button", { name: "Withdraw" }));
    expect(await within(section()).findByText("October 2026 was withdrawn. It can be proposed again.")).toBeInTheDocument();
    expect(await within(section()).findByRole("button", { name: "Close October 2026" })).toBeInTheDocument();
  });

  it("proposes the next month when it can be closed", async () => {
    const user = renderClose({ options: { empty: true } });
    expect(await within(section()).findByText("None yet")).toBeInTheDocument();
    expect(within(section()).getByText("No month has been closed yet.")).toBeInTheDocument();
    await user.click(within(section()).getByRole("button", { name: "Close October 2026" }));
    await user.type(within(section()).getByLabelText("Reason"), "Every day closed");
    await user.click(within(section()).getByRole("button", { name: "Propose closing October 2026" }));
    expect(await within(section()).findByText("Closing October 2026 is proposed. A different member of staff must approve it.")).toBeInTheDocument();
    const close = await waiting();
    expect(within(close).getByText(/Proposed by you/)).toBeInTheDocument();
    expect(within(close).getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
  });

  it("says why a month cannot be proposed yet", async () => {
    renderClose({ options: { empty: true, refusal: "days_not_closed", closedThrough: "2026-10-27" } });
    expect(await within(section()).findByText("Every day in October 2026 must be closed in the ledger first. Days are closed through 27 Oct 2026.")).toBeInTheDocument();
    expect(within(section()).queryByRole("button", { name: "Close October 2026" })).not.toBeInTheDocument();
  });

  it("says when Finvest cannot be reached", async () => {
    renderClose({ api: notConnectedMonthCloseApi });
    expect(await within(section()).findByText("Month closes could not be loaded")).toBeInTheDocument();
  });

  it("words the API's refusals", () => {
    const refused = (code: string) => new ApiError("conflict", 409, [{ field_path: ["body"], code }]);
    expect(monthCloseErrorMessage(refused("books_changed"))).toMatch(/changed after it was proposed/);
    expect(monthCloseErrorMessage(refused("proposed_by_you"))).toMatch(/a different person must approve/);
    expect(monthCloseErrorMessage(refused("close_months_in_order"))).toMatch(/Months close in order/);
    expect(refusalText("books_do_not_balance", "October 2026", null)).toMatch(/do not balance/);
    expect(refusalText("days_not_closed", "October 2026", null)).toMatch(/No day is closed yet/);
  });
});

describe("month closes over HTTP", () => {
  it("sends the routes the backend serves", async () => {
    const calls: { method: string; path: string; body: unknown; key: string | undefined }[] = [];
    const close = { id: "-Nclose", month: "2026-10", status: "proposed", books: [], books_sha256: "ab", reason: "r", proposed_by: "a", proposed_by_name: "A", proposed_at: "2026-11-14T06:00:00Z", decided_by: null, decided_by_name: null, decided_at: null, decision_reason: null, yours: false };
    const http: Http = {
      async request<T>(method: string, path: string, body?: unknown, init?: { idempotencyKey?: string }) {
        calls.push({ method, path, body, key: init?.idempotencyKey });
        if (method === "GET") return { last_closed: "2026-09", next: { month: "2026-10", closed_through: "2026-11-13", refusal: null }, closes: [close] } as T;
        return close as T;
      },
      download: () => Promise.reject(new Error("unused")),
    };
    const api = createHttpMonthCloseApi(http);
    const found = await api.getCloses();
    expect(found).toMatchObject({ lastClosed: "2026-09", next: { month: "2026-10", closedThrough: "2026-11-13", refusal: null } });
    expect(found.closes[0]).toMatchObject({ booksSha256: "ab", proposedByName: "A" });
    await api.propose("2026-10", "r", "k1");
    expect(calls[1]).toMatchObject({ method: "POST", path: "/staff/finance/month-closes", body: { month: "2026-10", reason: "r" }, key: "k1" });
    await api.decide("-Nclose", "approve", "ok", "k2");
    expect(calls[2]).toMatchObject({ path: "/staff/finance/month-closes/-Nclose/approve", key: "k2" });
    await api.decide("-Nclose", "decline", "no", "k3");
    expect(calls[3]).toMatchObject({ path: "/staff/finance/month-closes/-Nclose/decline", key: undefined });
  });
});
