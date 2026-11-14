import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import type { Directory } from "@/directory/api.ts";
import type { CalendarOut } from "@finvest/api-client/staff";
import { AuctionSection } from "./Auctions.tsx";
import {
  AuctionError,
  notConnectedAuctionsApi,
  type AuctionsApi,
} from "./auctions.ts";
import { createHttpAuctionsApi, toAuctionError } from "./auctionsHttp.ts";
import { createStubAuctionsApi } from "./auctionsStub.ts";

const TODAY = "2026-11-14";
const PRODUCTS = [
  { code: "TREA-91", name: "91 Day Treasury Bill" },
  { code: "TREA-182", name: "182 Day Treasury Bill" },
];

function renderCalendar(
  api: AuctionsApi = createStubAuctionsApi(
    { name: "Ada" },
    "sample",
    () => TODAY,
    0,
  ),
  onChanged = vi.fn(),
) {
  let keys = 0;
  render(
    <AuctionSection
      api={api}
      products={PRODUCTS}
      today={TODAY}
      newKey={() => `k-${++keys}`}
      onChanged={onChanged}
    />,
  );
  return { user: userEvent.setup(), onChanged };
}

const coming = () =>
  screen.findByRole("region", { name: "Auction dates from today on" });

describe("auction calendar", () => {
  it("lists the dates from today on, with a cancelled one and who cancelled it", async () => {
    renderCalendar();
    const table = await coming();
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(5);
    const moved = within(table).getByText(/Cancelled by Grace/);
    expect(moved).toHaveTextContent(
      "The Bank moved it to Wednesday 18 November",
    );
    expect(
      within(rows[1] as HTMLElement).getByRole("rowheader"),
    ).toHaveTextContent("Wed 18 Nov");
    expect(screen.getByText("Past month (2)")).toBeInTheDocument();
  });

  it("enters a date and tells the desk", async () => {
    const { user, onChanged } = renderCalendar();
    await coming();
    const form = screen.getByRole("form", { name: "Enter an auction date" });
    await user.selectOptions(
      within(form).getByLabelText("Treasury bill"),
      "TREA-182",
    );
    await user.type(
      within(form).getByLabelText("Day of the auction"),
      "2026-12-16",
    );
    await user.click(
      within(form).getByRole("button", { name: "Add the date" }),
    );
    const table = await coming();
    expect(await within(table).findByText("Wed 16 Dec")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("refuses a past day before sending, and a day already entered after", async () => {
    const api = createStubAuctionsApi(
      { name: "Ada" },
      "sample",
      () => TODAY,
      0,
    );
    const add = vi.spyOn(api, "add");
    const { user } = renderCalendar(api);
    await coming();
    const form = screen.getByRole("form", { name: "Enter an auction date" });
    const day = within(form).getByLabelText("Day of the auction");
    await user.type(day, "2026-11-13");
    await user.click(
      within(form).getByRole("button", { name: "Add the date" }),
    );
    expect(
      within(form).getByText(
        "That day has passed. Enter today or a later day.",
      ),
    ).toBeInTheDocument();
    expect(add).not.toHaveBeenCalled();

    await user.clear(day);
    await user.type(day, "2026-11-18");
    await user.click(
      within(form).getByRole("button", { name: "Add the date" }),
    );
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "That auction is already in the calendar for this bill.",
    );
  });

  it("cancels a date only with a reason", async () => {
    const { user, onChanged } = renderCalendar();
    const table = await coming();
    await user.click(
      within(table).getByRole("button", {
        name: "Cancel date for 182 Day Treasury Bill on 2 Dec 2026",
      }),
    );
    const form = screen.getByRole("form", {
      name: "Cancel the 182 Day Treasury Bill auction on 2 Dec 2026",
    });
    await user.click(
      within(form).getByRole("button", { name: "Cancel the date" }),
    );
    expect(
      within(form).getByText(/Say why the date is cancelled/),
    ).toBeInTheDocument();
    await user.type(
      within(form).getByLabelText("Why is it cancelled?"),
      "The Bank moved it to 3 December",
    );
    await user.click(
      within(form).getByRole("button", { name: "Cancel the date" }),
    );
    expect(
      await within(table).findByText(/The Bank moved it to 3 December/),
    ).toBeInTheDocument();
    expect(
      within(table).queryByRole("button", {
        name: "Cancel date for 182 Day Treasury Bill on 2 Dec 2026",
      }),
    ).not.toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("shows a reader the dates with no way to change them", async () => {
    const api = createStubAuctionsApi(
      { name: "Ada" },
      "sample",
      () => TODAY,
      0,
    );
    const reader: AuctionsApi = {
      ...api,
      getCalendar: async () => ({
        ...(await api.getCalendar()),
        mayChange: false,
      }),
    };
    renderCalendar(reader);
    const table = await coming();
    expect(within(table).queryByRole("button")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("form", { name: "Enter an auction date" }),
    ).not.toBeInTheDocument();
  });

  it("says when nothing is entered", async () => {
    renderCalendar(
      createStubAuctionsApi({ name: "Ada" }, "empty", () => TODAY, 0),
    );
    expect(
      await screen.findByText(/No auction dates are entered from today on/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("form", { name: "Enter an auction date" }),
    ).toBeInTheDocument();
  });

  it("offers to try again when the calendar cannot be read", async () => {
    renderCalendar(notConnectedAuctionsApi);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "The auction calendar is not connected to the Finvest service yet.",
    );
    expect(
      within(alert).getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });
});

describe("auction calendar over the API", () => {
  const json: CalendarOut = {
    auctions: [
      {
        id: "-Nauction1",
        product_code: "TREA-91",
        auction_date: "2026-11-18",
        added_by: "-Nstaffada",
        added_at: "2026-11-10T09:03:00Z",
        cancelled_by: null,
        cancelled_at: null,
        cancel_reason: null,
      },
    ],
    may_change: true,
  };
  const names: Directory = {
    staffName: (id) => Promise.resolve(id === "-Nstaffada" ? "Ada" : null),
  } as Directory;

  it("reads the calendar and names who entered each date", async () => {
    const request = vi.fn().mockResolvedValue(json);
    const api = createHttpAuctionsApi(
      { request, download: vi.fn() } as unknown as Http,
      names,
    );
    const calendar = await api.getCalendar();
    expect(request).toHaveBeenCalledWith("GET", "/staff/dealing/auctions");
    expect(calendar).toEqual({
      auctions: [
        {
          id: "-Nauction1",
          productCode: "TREA-91",
          auctionDate: "2026-11-18",
          addedBy: "Ada",
          addedAt: "2026-11-10T09:03:00Z",
          cancelledBy: null,
          cancelledAt: null,
          cancelReason: null,
        },
      ],
      mayChange: true,
    });
  });

  it("sends each command with its key", async () => {
    const request = vi.fn().mockResolvedValue(json);
    const api = createHttpAuctionsApi(
      { request, download: vi.fn() } as unknown as Http,
      names,
    );
    await api.add("k-1", "TREA-91", "2026-12-02");
    expect(request).toHaveBeenCalledWith(
      "POST",
      "/staff/dealing/auctions",
      { product_code: "TREA-91", auction_date: "2026-12-02" },
      { idempotencyKey: "k-1" },
    );
    await api.cancel("k-2", "-Nauction1", "Moved");
    expect(request).toHaveBeenCalledWith(
      "POST",
      "/staff/dealing/auctions/-Nauction1/cancel",
      { reason: "Moved" },
      { idempotencyKey: "k-2" },
    );
  });

  it("turns refusals into the screen's own", () => {
    const refused = (status: number, code: string, field?: string) =>
      new ApiError(
        code,
        status,
        field ? [{ field_path: ["auction_date"], code: field }] : [],
      );
    expect(
      toAuctionError(
        refused(422, "validation_failed", "auction_already_entered"),
      ).code,
    ).toBe("auction_already_entered");
    expect(
      toAuctionError(refused(422, "validation_failed", "something_else")).code,
    ).toBe("invalid");
    expect(toAuctionError(refused(409, "conflict")).code).toBe("conflict");
    expect(toAuctionError(refused(403, "forbidden")).code).toBe("forbidden");
    expect(toAuctionError(new TypeError("offline")).code).toBe("unavailable");
    expect(toAuctionError(new AuctionError("not_found")).code).toBe(
      "not_found",
    );
  });
});
