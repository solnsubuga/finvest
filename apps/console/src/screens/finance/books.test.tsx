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
    expect(screen.getByText(/ioTec’s charge on each mobile money payment in is not booked yet/)).toBeInTheDocument();
  });

  it("sends the closed days not yet sent and then shows them as sent", async () => {
    const { user, save } = renderBooks();
    const button = await screen.findByRole("button", { name: "Send 11 Nov 2026 to 12 Nov 2026" });
    await user.click(button);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0]![0].filename).toBe("finvest-journal-ugx-2026-11-11-to-2026-11-12.csv");
    await screen.findByText("Every closed day has been sent. The next one can go once it is closed on the Reconciliation screen.");
    expect(screen.getByText("Last journal sent").parentElement).toHaveTextContent("12 Nov 2026");
    expect(screen.getAllByText(/by Amina/)).toHaveLength(1);
  });

  it("says what to do when nothing is in the books and no day is closed", async () => {
    renderBooks(createStubBooksApi(ME, { today: TODAY, empty: true }));
    await screen.findByText(/Nothing is in the shilling books for November 2026 yet/);
    expect(screen.getByText(/Close a day on the Reconciliation screen first/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Send/ })).not.toBeInTheDocument();
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

  it("shows a refused send without losing the screen", async () => {
    const stub = createStubBooksApi(ME, { today: TODAY });
    const { user } = renderBooks({ ...stub, exportJournal: () => Promise.reject(new BooksError("day_not_closed")) });
    await user.click(await screen.findByRole("button", { name: /^Send/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only days closed on the Reconciliation screen can be sent");
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
    });
    const download = vi.fn().mockResolvedValue(new Blob(["x"]));
    const api = createHttpBooksApi({ request, download } as unknown as Http);
    const books = await api.getBooks("UGX", "2026-11");
    expect(request).toHaveBeenCalledWith("GET", "/staff/finance?currency=UGX&month=2026-11");
    expect(books.nextExport).toEqual({ dateFrom: "2026-11-12", dateTo: "2026-11-12" });
    const file = await api.exportJournal("USD", { dateFrom: "2026-11-12", dateTo: "2026-11-12" });
    expect(download).toHaveBeenCalledWith("/staff/finance/journal.csv?currency=USD&date_from=2026-11-12&date_to=2026-11-12");
    expect(file.filename).toBe("finvest-journal-usd-2026-11-12.csv");
    expect(journalFilename("UGX", { dateFrom: "2026-11-01", dateTo: "2026-11-03" })).toBe("finvest-journal-ugx-2026-11-01-to-2026-11-03.csv");
  });

  it("turns the API's refusals into the screen's codes", () => {
    const fields = (code: string) => [{ field_path: ["date_to"], code }];
    expect(toBooksError(new ApiError("conflict", 409, fields("day_not_closed"))).code).toBe("day_not_closed");
    expect(toBooksError(new ApiError("validation_failed", 422, fields("too_many_days"))).code).toBe("too_many_days");
    expect(toBooksError(new ApiError("validation_failed", 422, fields("bad_month"))).code).toBe("invalid");
    expect(toBooksError(new ApiError("forbidden", 403)).code).toBe("forbidden");
    expect(toBooksError(new ApiError("internal_error", 500)).code).toBe("unavailable");
    expect(toBooksError(new Error("network")).code).toBe("unavailable");
  });

  it("writes amounts without their currency for the table", () => {
    expect(formatNumber({ minor: 1_234_567, currency: "UGX" })).toBe("1,234,567");
    expect(formatNumber({ minor: -725, currency: "USD" })).toBe("-7.25");
  });
});
