import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { notConnectedTaxApi, TaxError, toTaxError, type TaxApi } from "./api.ts";
import { formatAmount, parseAmount } from "./format.ts";
import { createHttpTaxApi } from "./http.ts";
import { FinanceScreen } from "./index.tsx";
import { createStubTaxApi } from "./stub.ts";
import { createStubBooksApi } from "./booksStub.ts";

const ME = { id: "sample-me", name: "Amina" };
const TODAY = "2026-11-13";
// What the sample's five clients had withheld in a whole month: 4,674 + 9,349 + 1,232 + 21,037 + 2,671.
const OCTOBER = 38_963;

function renderFinance(api: TaxApi = createStubTaxApi(ME, { today: TODAY }), canPay = true) {
  let keys = 0;
  const save = vi.fn();
  render(<FinanceScreen api={api} canPay={canPay} newKey={() => `key-${++keys}`} today={() => TODAY} save={save} />);
  return { api, save, user: userEvent.setup() };
}

function monthRow(month: string): HTMLElement {
  return within(screen.getByRole("table", { name: "Withholding tax by month" }))
    .getByRole("rowheader", { name: month })
    .closest("tr") as HTMLElement;
}

const payment = (month: string) => screen.findAllByRole("article", { name: `Payment for ${month}` });

describe("finance screen", () => {
  it("shows the books above withholding tax, and both follow the currency", async () => {
    const books = createStubBooksApi(ME, { today: TODAY });
    const getBooks = vi.spyOn(books, "getBooks");
    render(<FinanceScreen api={createStubTaxApi(ME, { today: TODAY })} booksApi={books} canPay today={() => TODAY} />);
    await screen.findByRole("table", { name: "Books for November 2026" });
    expect(screen.getByRole("heading", { name: "Withholding tax" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "USD" }));
    await screen.findByText(/Nothing is in the dollar books for November 2026 yet/);
    expect(getBooks).toHaveBeenLastCalledWith("USD", "2026-11");
  });

  it("shows each month against what was paid to URA and when it is due", async () => {
    renderFinance();
    await screen.findByRole("table", { name: "Withholding tax by month" });
    expect(within(monthRow("November 2026")).getByText("This month")).toBeInTheDocument();
    const october = monthRow("October 2026");
    expect(october).toHaveTextContent(formatAmount({ minor: OCTOBER, currency: "UGX" }));
    expect(october).toHaveTextContent("15 Nov 2026");
    expect(within(october).getByText("Waiting for approval")).toBeInTheDocument();
    expect(within(monthRow("September 2026")).getByText("Paid")).toBeInTheDocument();
    expect(within(monthRow("February 2026")).getByText("Nothing withheld")).toBeInTheDocument();
    expect(screen.getByText("Owed for ended months").parentElement).toHaveTextContent("UGX 0");
    expect(screen.getByText("Next payment due").parentElement).toHaveTextContent("Nothing due");
  });

  it("lets a second person approve a payment someone else recorded", async () => {
    const { user } = renderFinance();
    const [card] = await payment("October 2026");
    expect(within(card!).getByText("Grace Atim")).toBeInTheDocument();
    await user.click(within(card!).getByRole("button", { name: "Approve and post" }));
    const [approved] = await payment("October 2026");
    expect(within(approved!).getByText("Approved and posted")).toBeInTheDocument();
    expect(within(approved!).getByText("Amina")).toBeInTheDocument();
    expect(within(monthRow("October 2026")).getByText("Paid")).toBeInTheDocument();
  });

  it("records a payment for a second person once the first was declined", async () => {
    const { user } = renderFinance();
    const [card] = await payment("October 2026");
    await user.click(within(card!).getByRole("button", { name: "Decline" }));
    await user.type(within(card!).getByLabelText("Why is it declined?"), "PRN not on URA's receipt");
    await user.click(within(card!).getByRole("button", { name: "Decline payment" }));
    await waitFor(() => expect(within(monthRow("October 2026")).getByText("To pay")).toBeInTheDocument());

    await user.click(within(monthRow("October 2026")).getByRole("button", { name: "Record a payment for October 2026" }));
    const form = screen.getByRole("form", { name: "Record a payment for October 2026" });
    expect(within(form).getByLabelText("Amount paid (UGX)")).toHaveValue(String(OCTOBER));
    await user.click(within(form).getByRole("button", { name: "Record for approval" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Enter URA's PRN");
    await user.type(within(form).getByLabelText("URA payment registration number (PRN)"), "PRN2210000999");
    await user.type(within(form).getByLabelText("Bank reference"), "FT26317ABC");
    await user.click(within(form).getByRole("button", { name: "Record for approval" }));

    await waitFor(() => expect(screen.queryByRole("form")).not.toBeInTheDocument());
    const cards = await payment("October 2026");
    const mine = cards.find((c) => within(c).queryByText("PRN2210000999"));
    expect(mine).toBeDefined();
    expect(within(mine!).getByText("Waiting for a second person")).toBeInTheDocument();
    expect(within(mine!).getByText("Another member of staff approves this. It is not posted until they do.")).toBeInTheDocument();
    expect(within(mine!).queryByRole("button")).not.toBeInTheDocument();
  });

  it("refuses more than the month still owes", async () => {
    const { user } = renderFinance();
    const [card] = await payment("October 2026");
    await user.click(within(card!).getByRole("button", { name: "Decline" }));
    await user.type(within(card!).getByLabelText("Why is it declined?"), "Wrong amount");
    await user.click(within(card!).getByRole("button", { name: "Decline payment" }));
    await user.click(await within(monthRow("October 2026")).findByRole("button", { name: "Record a payment for October 2026" }));
    const form = screen.getByRole("form", { name: "Record a payment for October 2026" });
    const amount = within(form).getByLabelText("Amount paid (UGX)");
    await user.clear(amount);
    await user.type(amount, String(OCTOBER + 1));
    await user.type(within(form).getByLabelText("URA payment registration number (PRN)"), "PRN1");
    await user.type(within(form).getByLabelText("Bank reference"), "FT1");
    await user.click(within(form).getByRole("button", { name: "Record for approval" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("more than the month still owes");
  });

  it("lists one month client by client and hands over the file for URA", async () => {
    const { user, save } = renderFinance();
    await user.click(await within(await screen.findByRole("table", { name: "Withholding tax by month" })).findByRole("button", { name: "Clients for October 2026" }));
    const detail = await screen.findByRole("table", { name: "Clients with tax withheld in October 2026" });
    const okello = within(detail).getByRole("rowheader", { name: "OKELLO Peter" }).closest("tr") as HTMLElement;
    expect(okello).toHaveTextContent("Not given");
    expect(okello).toHaveTextContent("UGX 9,349");
    expect(within(detail).getByRole("rowheader", { name: "5 clients" }).closest("tr")).toHaveTextContent("UGX 38,963");
    await user.click(screen.getByRole("button", { name: "Download for URA (CSV)" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0]![0].filename).toBe("finvest-withholding-ugx-2026-10.csv");
  });

  it("hides recording for a role without the permission", async () => {
    renderFinance(createStubTaxApi(ME, { today: TODAY }), false);
    await screen.findByRole("table", { name: "Withholding tax by month" });
    expect(screen.queryByRole("button", { name: /Record a payment/ })).not.toBeInTheDocument();
  });

  it("says when nothing has been withheld yet", async () => {
    renderFinance(createStubTaxApi(ME, { today: TODAY, empty: true }));
    expect(await screen.findByRole("heading", { name: "No tax withheld yet" })).toBeInTheDocument();
    expect(screen.getByText("Held for URA now").parentElement).toHaveTextContent("UGX 0");
  });

  it("says when Finvest cannot be reached, and tries again", async () => {
    const api: TaxApi = { ...notConnectedTaxApi, getScreen: vi.fn().mockRejectedValue(new TaxError("unavailable")) };
    const { user } = renderFinance(api);
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(api.getScreen).toHaveBeenCalledTimes(2);
  });
});

describe("amounts", () => {
  it("reads whole shillings and dollars with cents, never through a float", () => {
    expect(parseAmount("38,963", "UGX")).toBe(38_963);
    expect(parseAmount("38963.5", "UGX")).toBeNull();
    expect(parseAmount("7.25", "USD")).toBe(725);
    expect(parseAmount("7.2", "USD")).toBe(720);
    expect(parseAmount("0", "UGX")).toBeNull();
    expect(formatAmount({ minor: -50, currency: "UGX" })).toBe("-UGX 50");
    expect(formatAmount({ minor: 36, currency: "USD" })).toBe("$0.36");
  });
});

describe("tax over HTTP", () => {
  it("sends a payment with its key and reads the screen back", async () => {
    const request = vi.fn().mockResolvedValue({
      currency: "UGX",
      held_for_ura: { minor: 900, currency: "UGX" },
      months: [],
      remittances: [],
    });
    const api = createHttpTaxApi({ request, download: vi.fn() } as unknown as Http);
    const screenBack = await api.record("key-1", {
      currency: "UGX",
      month: "2026-10",
      amountMinor: 900,
      uraReference: "PRN1",
      bankReference: "FT1",
      paidOn: "2026-11-12",
      note: "October",
    });
    expect(screenBack.heldForUra.minor).toBe(900);
    expect(request).toHaveBeenCalledWith(
      "POST",
      "/staff/tax/remittances",
      {
        currency: "UGX",
        month: "2026-10",
        amount_minor: 900,
        ura_reference: "PRN1",
        bank_reference: "FT1",
        paid_on: "2026-11-12",
        note: "October",
      },
      { idempotencyKey: "key-1" },
    );
  });

  it("turns the API's refusals into the screen's", () => {
    const field = (code: string) => new ApiError("validation_failed", 422, [{ field_path: ["amount"], code }]);
    expect(toTaxError(field("more_than_outstanding")).code).toBe("more_than_outstanding");
    expect(toTaxError(field("something_new")).code).toBe("invalid");
    expect(toTaxError(new ApiError("conflict", 409)).code).toBe("conflict");
    expect(toTaxError(new ApiError("internal_error", 500)).code).toBe("unavailable");
  });
});
