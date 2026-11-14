import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { BooksSection } from "./Books.tsx";
import { BooksError, notConnectedBooksApi, toBooksError, type BooksApi } from "./books.ts";
import { createHttpBooksApi, journalFilename } from "./booksHttp.ts";
import { createStubBooksApi } from "./booksStub.ts";
import { formatNumber } from "./format.ts";

const ME = { id: "sample-me", name: "Amina" };
const TODAY = "2026-11-14";

function renderBooks(api: BooksApi = createStubBooksApi(ME, { today: TODAY })) {
  const save = vi.fn();
  render(<BooksSection api={api} currency="UGX" today={TODAY} save={save} />);
  return { api, save, user: userEvent.setup() };
}

describe("books", () => {
  it("shows each account's month and Finvest's income and costs", async () => {
    renderBooks();
    const table = await screen.findByRole("table", { name: "Books for November 2026" });
    const trust = within(table).getByRole("rowheader", { name: /Trust bank account/ }).closest("tr") as HTMLElement;
    expect(trust).toHaveTextContent("18,146,250,000");
    expect(trust).toHaveTextContent("18,306,950,000");
    const costs = screen.getByText("Costs this month").parentElement as HTMLElement;
    expect(costs).toHaveTextContent("UGX 6,394,500");
    expect(screen.getByText("Income this month").parentElement).toHaveTextContent("UGX 10,270,000");
    expect(screen.getByText("Net this month").parentElement).toHaveTextContent("UGX 3,875,500");
    expect(screen.getByText(/ioTec fees include ioTec’s charge on each mobile money payment in, rounded up/)).toBeInTheDocument();
    expect(screen.queryByText(/payment in is not booked yet/)).not.toBeInTheDocument();
  });

  it("downloads the closed days not yet downloaded and then lists them", async () => {
    const { user, save } = renderBooks();
    // The last file was for QuickBooks, so the next one is too until another is picked.
    expect(await screen.findByRole("combobox", { name: /File for/ })).toHaveValue("quickbooks");
    const button = await screen.findByRole("button", { name: "Download 11 Nov 2026 to 12 Nov 2026 for QuickBooks Online" });
    await user.click(button);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0]![0].filename).toBe("finvest-journal-quickbooks-ugx-2026-11-11-to-2026-11-12.csv");
    await screen.findByText("Every closed day has been downloaded. The next one can be once it is closed on the Reconciliation screen.");
    expect(screen.getByText("Journal downloaded through").parentElement).toHaveTextContent("12 Nov 2026");
    expect(screen.getAllByText(/by Amina/)).toHaveLength(1);
    expect(screen.getByText(/Nothing is sent to the accounting system from here/)).toBeInTheDocument();
  });

  it("downloads a past file again, and says so when saving fails", async () => {
    const stub = createStubBooksApi(ME, { today: TODAY });
    const exportJournal = vi.fn(stub.exportJournal);
    const save = vi.fn(() => {
      throw new Error("disk full");
    });
    render(<BooksSection api={{ ...stub, exportJournal }} currency="UGX" today={TODAY} save={save} />);
    const user = userEvent.setup();
    const again = await screen.findAllByRole("button", { name: /^Download again/ });
    await user.click(again[0]!);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [, days, format] = exportJournal.mock.calls[0]!;
    expect(days.dateTo >= days.dateFrom).toBe(true);
    // Again in the format it was first downloaded in.
    expect(format).toBe("quickbooks");
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be saved\. Download it again from the list below\./);
    expect(screen.getAllByRole("button", { name: /^Download again/ }).length).toBeGreaterThan(again.length);
  });

  it("says what to do when nothing is in the books and no day is closed", async () => {
    renderBooks(createStubBooksApi(ME, { today: TODAY, empty: true }));
    await screen.findByText(/Nothing is in the shilling books for November 2026 yet/);
    expect(screen.getByText(/Close a day on the Reconciliation screen first/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Download/ })).not.toBeInTheDocument();
  });

  it("offers to try again when the books cannot be loaded", async () => {
    const { user } = renderBooks({ ...notConnectedBooksApi, getBooks: () => Promise.reject(new BooksError("unavailable")) });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("We could not reach Finvest");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("loads another month when it is picked", async () => {
    const getBooks = vi.fn(createStubBooksApi(ME, { today: TODAY }).getBooks);
    const { user } = renderBooks({ ...createStubBooksApi(ME, { today: TODAY }), getBooks });
    await screen.findByRole("table", { name: "Books for November 2026" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Month" }), "2026-10");
    await screen.findByRole("table", { name: "Books for October 2026" });
    expect(getBooks).toHaveBeenLastCalledWith("UGX", "2026-10");
    expect(screen.getByText("Income in October 2026")).toBeInTheDocument();
  });

  it("offers each accounting system and says when one cannot be downloaded yet", async () => {
    const { user, save } = renderBooks();
    const picker = await screen.findByRole("combobox", { name: /File for/ });
    expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Finvest CSV",
      "QuickBooks Online",
      "Xero",
      "Sage Accounting (not set up)",
      "Zoho Books (not set up)",
    ]);
    await user.selectOptions(picker, "sage");
    expect(screen.getByText(/No Sage Accounting account codes are approved yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Download 11/ })).toBeDisabled();
    await user.selectOptions(picker, "xero");
    expect(screen.getByText(/1 account has no Xero account yet: fees\.provider\.iotec/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Download 11 .* for Xero$/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This account has no account in the accounting system yet: fees.provider.iotec. An admin names them under Settings, Accounting system codes.",
    );
    await user.selectOptions(picker, "csv");
    await user.click(screen.getByRole("button", { name: "Download 11 Nov 2026 to 12 Nov 2026" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0]![0].filename).toBe("finvest-journal-ugx-2026-11-11-to-2026-11-12.csv");
  });

  it("keeps dollar journals to the Finvest CSV", async () => {
    const save = vi.fn();
    render(<BooksSection api={createStubBooksApi(ME, { today: TODAY })} currency="USD" today={TODAY} save={save} />);
    const picker = await screen.findByRole("combobox", { name: /File for/ });
    expect(picker).toHaveValue("csv");
    await userEvent.setup().selectOptions(picker, "quickbooks");
    expect(screen.getByText(/QuickBooks Online takes the journal in shillings only/)).toBeInTheDocument();
  });

  it("shows a refused download without losing the screen", async () => {
    const stub = createStubBooksApi(ME, { today: TODAY });
    const { user } = renderBooks({ ...stub, exportJournal: () => Promise.reject(new BooksError("day_not_closed")) });
    await user.click(await screen.findByRole("button", { name: /^Download 11/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only days closed on the Reconciliation screen can be downloaded");
    expect(screen.getByRole("table", { name: "Books for November 2026" })).toBeInTheDocument();
  });
});

describe("books over HTTP", () => {
  it("asks for the month and the days, and names the file", async () => {
    const request = vi.fn().mockResolvedValue({
      currency: "UGX",
      month: "2026-11",
      date_from: "2026-11-01",
      date_to: "2026-11-14",
      accounts: [],
      income: {
        income: [],
        costs: [],
        income_total: { minor: 0, currency: "UGX" },
        cost_total: { minor: 0, currency: "UGX" },
        net: { minor: 0, currency: "UGX" },
      },
      closed_through: "2026-11-12",
      sent_through: null,
      next_export: { date_from: "2026-11-12", date_to: "2026-11-12" },
      exports: [],
      formats: [{ format: "xero", name: "Xero", ready: true, unmapped: [], currencies: ["UGX"] }],
    });
    const download = vi.fn().mockResolvedValue(new Blob(["x"]));
    const api = createHttpBooksApi({ request, download } as unknown as Http);
    const books = await api.getBooks("UGX", "2026-11");
    expect(request).toHaveBeenCalledWith("GET", "/staff/finance?currency=UGX&month=2026-11");
    expect(books.nextExport).toEqual({ dateFrom: "2026-11-12", dateTo: "2026-11-12" });
    expect(books.formats).toEqual([{ format: "xero", name: "Xero", ready: true, unmapped: [], currencies: ["UGX"] }]);
    const file = await api.exportJournal("USD", { dateFrom: "2026-11-12", dateTo: "2026-11-12" }, "csv");
    // A POST, because the server records each export.
    expect(download).toHaveBeenCalledWith("/staff/finance/journal.csv", {
      method: "POST",
      body: { currency: "USD", date_from: "2026-11-12", date_to: "2026-11-12", format: "csv" },
    });
    expect(file.filename).toBe("finvest-journal-usd-2026-11-12.csv");
    const xero = await api.exportJournal("UGX", { dateFrom: "2026-11-12", dateTo: "2026-11-12" }, "xero");
    expect(xero.filename).toBe("finvest-journal-xero-ugx-2026-11-12.csv");
    expect(journalFilename("UGX", { dateFrom: "2026-11-01", dateTo: "2026-11-03" })).toBe("finvest-journal-ugx-2026-11-01-to-2026-11-03.csv");
  });

  it("turns the API's refusals into the screen's codes", () => {
    const fields = (code: string) => [{ field_path: ["date_to"], code }];
    expect(toBooksError(new ApiError("conflict", 409, fields("day_not_closed"))).code).toBe("day_not_closed");
    expect(toBooksError(new ApiError("validation_failed", 422, fields("too_many_days"))).code).toBe("too_many_days");
    expect(toBooksError(new ApiError("validation_failed", 422, fields("bad_month"))).code).toBe("invalid");
    expect(toBooksError(new ApiError("forbidden", 403)).code).toBe("forbidden");
    const unmapped = toBooksError(
      new ApiError("conflict", 409, [
        { field_path: ["codes", "bank.trust"], code: "not_mapped" },
        { field_path: ["codes", "iotec.wallet"], code: "not_mapped" },
      ]),
    );
    expect([unmapped.code, unmapped.codes]).toEqual(["codes_not_mapped", ["bank.trust", "iotec.wallet"]]);
    expect(toBooksError(new ApiError("validation_failed", 422, [{ field_path: ["format"], code: "currency_not_supported" }])).code).toBe(
      "currency_not_supported",
    );
    expect(toBooksError(new ApiError("internal_error", 500)).code).toBe("unavailable");
    expect(toBooksError(new Error("network")).code).toBe("unavailable");
  });

  it("writes amounts without their currency for the table", () => {
    expect(formatNumber({ minor: 1_234_567, currency: "UGX" })).toBe("1,234,567");
    expect(formatNumber({ minor: -725, currency: "USD" })).toBe("-7.25");
  });
});
