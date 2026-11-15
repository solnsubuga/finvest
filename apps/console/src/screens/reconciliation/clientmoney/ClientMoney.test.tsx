import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { ClientMoneyError, notConnectedClientMoneyApi, type ClientMoneyApi } from "./api.ts";
import { ClientMoney } from "./ClientMoney.tsx";
import { createHttpClientMoneyApi } from "./http.ts";
import { createStubClientMoneyApi } from "./stub.ts";

const panel = () => screen.getByRole("region", { name: "Client money check" });
const side = (name: string) => within(panel()).getByRole("region", { name });

describe("the client money check", () => {
  it("shows what is owed against what is held, and that they tie", async () => {
    render(<ClientMoney api={createStubClientMoneyApi(0)} />);
    expect(await screen.findByText("Ties")).toBeInTheDocument();
    expect(screen.getByText(/At the end of Sat,? 14 Nov 2026, what we hold for clients equals what we owe them/)).toBeInTheDocument();
    const owed = side("Owed to clients");
    expect(within(owed).getByText("Interest earned, not yet paid")).toBeInTheDocument();
    expect(within(owed).getByText(/UGX 120,000,000 still to come/)).toBeInTheDocument();
    const held = side("Held for clients");
    expect(within(held).getByText("ioTec wallet, MTN, statement")).toBeInTheDocument();
    expect(within(held).getByText("Trust bank account, statement")).toBeInTheDocument();
    expect(within(held).getByText(/Custodian statement checked for this day, every maturity matching/)).toBeInTheDocument();
    expect(within(held).getByText("Less: Finvest's own money in the buffer")).toBeInTheDocument();
    expect(within(held).getByText("-UGX 250,000,000")).toBeInTheDocument();
    // Earnings below zero add to what is held, as the interest accrued is not yet paid for.
    expect(within(held).getByText("Plus: Finvest's earnings so far, income less costs")).toBeInTheDocument();
    expect(within(held).getByText("UGX 35,600,000")).toBeInTheDocument();
    expect(within(owed).getAllByText("UGX 4,635,200,000")).toHaveLength(1);
    expect(within(held).getAllByText("UGX 4,635,200,000")).toHaveLength(1);
    expect(screen.queryByRole("region", { name: "Why it does not tie" })).not.toBeInTheDocument();
  });

  it("says by how much it is short and what accounts for it, never naming the old platform", async () => {
    render(<ClientMoney api={createStubClientMoneyApi(0, "short")} />);
    expect(await screen.findByText("Short by UGX 18,500,000")).toBeInTheDocument();
    const why = within(panel()).getByRole("region", { name: "Why it does not tie" });
    expect(within(why).getByText("Differences still open on the statements")).toBeInTheDocument();
    expect(within(why).getByText("-UGX 500,000")).toBeInTheDocument();
    expect(within(why).getByText(/Balances carried over from the paper register, not yet in a pot/)).toBeInTheDocument();
    expect(within(why).queryByText("Not accounted for")).not.toBeInTheDocument();
    expect(within(side("Held for clients")).getByText(/last checked for Sat,? 7 Nov 2026, with 1 difference explained/)).toBeInTheDocument();
    expect(panel()).not.toHaveTextContent("1.0");
  });

  it("says plainly when nothing is closed yet", async () => {
    render(<ClientMoney api={createStubClientMoneyApi(0, "empty")} />);
    expect(await screen.findByText(/No day has been closed yet/)).toBeInTheDocument();
  });

  it("offers another try when the check cannot be loaded", async () => {
    const get = vi.fn<ClientMoneyApi["get"]>().mockRejectedValueOnce(new ClientMoneyError("unavailable")).mockResolvedValueOnce(null);
    render(<ClientMoney api={{ get }} />);
    expect(await screen.findByText("The client money check could not be loaded")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/No day has been closed yet/)).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("shows a figure nothing accounts for as a warning", async () => {
    const stub = createStubClientMoneyApi(0, "short");
    const check = await stub.get();
    if (!check) throw new Error("the sample has a check");
    render(<ClientMoney api={{ get: () => Promise.resolve({ ...check, unexplained: { minor: 1_000, currency: "UGX" } }) }} />);
    expect(await screen.findByText("Not accounted for")).toBeInTheDocument();
    expect(screen.getByText(/a figure may have been read wrongly/)).toBeInTheDocument();
  });

  it("is not connected without a backend", async () => {
    render(<ClientMoney api={notConnectedClientMoneyApi} />);
    expect(await screen.findByText(/not connected to the Finvest service yet/)).toBeInTheDocument();
  });
});

describe("the client money check over HTTP", () => {
  const amount = (minor: number) => ({ minor, currency: "UGX" as const });
  const json = {
    business_date: "2026-11-14",
    standing: "ties",
    owed: amount(10),
    interest_owed: amount(2),
    pots: [
      { source: "bank", account_reference: "trust", name: "Trust bank account", statement_closing: amount(10), ledger_closing: amount(10), open_differences: amount(0) },
    ],
    on_statements: amount(10),
    bills: amount(0),
    custodian: { check_id: "-OdCustodian000003", statement_date: "2026-11-14", differences: 0 },
    discount_to_come: amount(0),
    not_client_money: [{ code: "earnings", name: "Earnings", amount: amount(0) }],
    not_client_money_total: amount(0),
    held: amount(10),
    difference: amount(0),
    open_differences: amount(0),
    not_on_statement: [],
    not_on_statement_total: amount(0),
    unexplained: amount(0),
  };

  it("reads the route and turns its names into the screen's", async () => {
    const request = vi.fn().mockResolvedValue({ check: json });
    const found = await createHttpClientMoneyApi({ request } as unknown as Http).get();
    expect(request).toHaveBeenCalledWith("GET", "/staff/reconciliation/client-money");
    expect(found?.pots[0]?.accountReference).toBe("trust");
    expect(found?.custodian?.statementDate).toBe("2026-11-14");
    expect(found?.interestOwed).toEqual(amount(2));
  });

  it("answers null before any day is closed, and words a refusal", async () => {
    const empty = vi.fn().mockResolvedValue({ check: null });
    expect(await createHttpClientMoneyApi({ request: empty } as unknown as Http).get()).toBeNull();
    const refused = vi.fn().mockRejectedValue(new ApiError("forbidden", 403, []));
    await expect(createHttpClientMoneyApi({ request: refused } as unknown as Http).get()).rejects.toMatchObject({ code: "forbidden" });
  });
});
