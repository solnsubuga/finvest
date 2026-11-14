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

const stub = (kind: "sample" | "empty" = "sample") =>
  createStubAuctionsApi({ name: "Ada" }, kind, () => TODAY, 0);

function renderCalendar(api: AuctionsApi = stub(), onChanged = vi.fn()) {
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
const row = (table: HTMLElement, day: string, bill: string) =>
  within(table)
    .getAllByRole("listitem")
    .find(
      (r) => r.textContent?.includes(day) && r.textContent.includes(bill),
    ) as HTMLElement;

describe("auction calendar", () => {
  it("lists the dates from today on, with what waits for a second person", async () => {
    renderCalendar();
    const table = await coming();
    expect(within(table).getAllByRole("listitem")).toHaveLength(6);
    expect(
      within(row(table, "16 Dec 2026", "91 Day")).getByText(
        "Waiting for a second person",
      ),
    ).toBeInTheDocument();
    const moving = row(table, "2 Dec 2026", "91 Day");
    expect(
      within(moving).getByText("Cancellation waiting for a second person"),
    ).toBeInTheDocument();
    expect(
      within(moving).getByText(
        /The Bank moved it to Thursday 3 December. Asked by Grace/,
      ),
    ).toBeInTheDocument();
    expect(
      within(row(table, "16 Nov 2026", "91 Day")).getByText("Cancelled"),
    ).toBeInTheDocument();
    expect(screen.getByText("Past month (2)")).toBeInTheDocument();
  });

  it("proposes a date, which waits for someone else and can be withdrawn", async () => {
    const { user, onChanged } = renderCalendar();
    await coming();
    const form = screen.getByRole("form", { name: "Propose an auction date" });
    await user.selectOptions(
      within(form).getByLabelText("Treasury bill"),
      "TREA-182",
    );
    await user.type(
      within(form).getByLabelText("Day of the auction"),
      "2026-12-16",
    );
    await user.click(
      within(form).getByRole("button", { name: "Propose the date" }),
    );
    const withdraw = await screen.findByRole("button", {
      name: "Withdraw the 182 Day Treasury Bill auction on 16 Dec 2026",
    });
    expect(
      screen.queryByRole("button", {
        name: "Approve the 182 Day Treasury Bill auction on 16 Dec 2026",
      }),
    ).not.toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledOnce();
    await user.click(withdraw);
    expect(
      await within(row(await coming(), "16 Dec 2026", "182 Day")).findByText(
        "Withdrawn",
      ),
    ).toBeInTheDocument();
  });

  it("refuses a past day before sending, and a day already in the calendar after", async () => {
    const api = stub();
    const propose = vi.spyOn(api, "propose");
    const { user } = renderCalendar(api);
    await coming();
    const form = screen.getByRole("form", { name: "Propose an auction date" });
    const day = within(form).getByLabelText("Day of the auction");
    await user.type(day, "2026-11-13");
    await user.click(
      within(form).getByRole("button", { name: "Propose the date" }),
    );
    expect(
      within(form).getByText(
        "That day has passed. Propose today or a later day.",
      ),
    ).toBeInTheDocument();
    expect(propose).not.toHaveBeenCalled();

    await user.clear(day);
    await user.type(day, "2026-11-18");
    await user.click(
      within(form).getByRole("button", { name: "Propose the date" }),
    );
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "That auction is already in the calendar for this bill",
    );
  });

  it("approves a date someone else proposed", async () => {
    const { user, onChanged } = renderCalendar();
    await coming();
    await user.click(
      screen.getByRole("button", {
        name: "Approve the 91 Day Treasury Bill auction on 16 Dec 2026",
      }),
    );
    const approved = row(await coming(), "16 Dec 2026", "91 Day");
    expect(await within(approved).findByText("Standing")).toBeInTheDocument();
    expect(within(approved).getByText("Approved by Ada")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("declines a cancellation only with a note, and the date stands", async () => {
    const { user } = renderCalendar();
    await coming();
    await user.click(
      screen.getByRole("button", {
        name: "Decline cancelling the 91 Day Treasury Bill auction on 2 Dec 2026",
      }),
    );
    const form = screen.getByRole("form", {
      name: "Decline cancelling the 91 Day Treasury Bill auction on 2 Dec 2026",
    });
    await user.click(within(form).getByRole("button", { name: "Decline it" }));
    expect(
      within(form).getByText(/Say why you are declining it/),
    ).toBeInTheDocument();
    await user.type(
      within(form).getByLabelText("Why are you declining it?"),
      "The Bank's notice still says 2 December",
    );
    await user.click(within(form).getByRole("button", { name: "Decline it" }));
    const kept = row(await coming(), "2 Dec 2026", "91 Day");
    expect(
      await within(kept).findByText(
        /Cancelling it was declined: The Bank's notice still says 2 December/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("form", { name: /Decline cancelling/ }),
    ).not.toBeInTheDocument();
  });

  it("proposes cancelling a standing date with a reason, which someone else approves", async () => {
    const { user } = renderCalendar();
    await coming();
    await user.click(
      screen.getByRole("button", {
        name: "Cancel date for the 182 Day Treasury Bill auction on 2 Dec 2026",
      }),
    );
    const form = screen.getByRole("form", {
      name: "Cancel the 182 Day Treasury Bill auction on 2 Dec 2026",
    });
    await user.click(
      within(form).getByRole("button", { name: "Propose cancelling it" }),
    );
    expect(
      within(form).getByText(/Say why the date is cancelled/),
    ).toBeInTheDocument();
    await user.type(
      within(form).getByLabelText("Why is it cancelled?"),
      "The Bank moved it to 3 December",
    );
    await user.click(
      within(form).getByRole("button", { name: "Propose cancelling it" }),
    );
    const moving = row(await coming(), "2 Dec 2026", "182 Day");
    expect(
      await within(moving).findByText(
        "Cancellation waiting for a second person",
      ),
    ).toBeInTheDocument();
    expect(
      within(moving).getByRole("button", {
        name: "Withdraw cancelling the 182 Day Treasury Bill auction on 2 Dec 2026",
      }),
    ).toBeInTheDocument();
    expect(
      within(moving).queryByRole("button", { name: /Approve cancelling/ }),
    ).not.toBeInTheDocument();
  });

  it("shows a reader the dates with no way to change them", async () => {
    const api = stub();
    const reader: AuctionsApi = {
      ...api,
      getCalendar: async () => {
        const calendar = await api.getCalendar();
        return {
          auctions: calendar.auctions.map((a) => ({
            ...a,
            allowed: [],
            cancellation: a.cancellation && { ...a.cancellation, allowed: [] },
          })),
          mayChange: false,
        };
      },
    };
    renderCalendar(reader);
    const table = await coming();
    expect(within(table).queryByRole("button")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("form", { name: "Propose an auction date" }),
    ).not.toBeInTheDocument();
  });

  it("says when nothing is in the calendar", async () => {
    renderCalendar(stub("empty"));
    expect(
      await screen.findByText(
        /No auction dates are in the calendar from today on/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("form", { name: "Propose an auction date" }),
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
        auction_date: "2026-12-02",
        state: "cancelling",
        proposed_by: "-Nstaffada",
        proposed_at: "2026-11-10T09:03:00Z",
        decided_by: "-Nstaffben",
        decided_at: "2026-11-10T10:00:00Z",
        decision_note: null,
        cancellation: {
          id: "-Ncancel1",
          reason: "Moved",
          status: "proposed",
          proposed_by: "-Nstaffben",
          proposed_at: "2026-11-13T14:20:00Z",
          decided_by: null,
          decided_at: null,
          decision_note: null,
          allowed: ["approve", "decline"],
        },
        allowed: [],
      },
    ],
    may_change: true,
  };
  const names = {
    staffName: (id: string) =>
      Promise.resolve(id === "-Nstaffada" ? "Ada" : null),
  } as unknown as Directory;
  const api = () => {
    const request = vi.fn().mockResolvedValue(json);
    return {
      request,
      auctions: createHttpAuctionsApi(
        { request, download: vi.fn() } as unknown as Http,
        names,
      ),
    };
  };

  it("reads the calendar and names the staff the directory knows", async () => {
    const { request, auctions } = api();
    const calendar = await auctions.getCalendar();
    expect(request).toHaveBeenCalledWith("GET", "/staff/dealing/auctions");
    expect(calendar.mayChange).toBe(true);
    expect(calendar.auctions[0]).toMatchObject({
      id: "-Nauction1",
      productCode: "TREA-91",
      auctionDate: "2026-12-02",
      state: "cancelling",
      proposedBy: "Ada",
      decidedBy: "-Nstaffben",
      cancellation: {
        id: "-Ncancel1",
        reason: "Moved",
        status: "proposed",
        proposedBy: "-Nstaffben",
        allowed: ["approve", "decline"],
      },
      allowed: [],
    });
  });

  it("sends each command to its route with its key", async () => {
    const { request, auctions } = api();
    await auctions.propose("k-1", "TREA-91", "2026-12-16");
    expect(request).toHaveBeenLastCalledWith(
      "POST",
      "/staff/dealing/auctions",
      { product_code: "TREA-91", auction_date: "2026-12-16" },
      { idempotencyKey: "k-1" },
    );
    await auctions.proposeCancel("k-2", "-Nauction1", "Moved");
    expect(request).toHaveBeenLastCalledWith(
      "POST",
      "/staff/dealing/auctions/-Nauction1/cancel",
      { reason: "Moved" },
      { idempotencyKey: "k-2" },
    );
    await auctions.approve("k-3", "-Ncancel1");
    expect(request).toHaveBeenLastCalledWith(
      "POST",
      "/staff/dealing/auctions/changes/-Ncancel1/approve",
      undefined,
      { idempotencyKey: "k-3" },
    );
    await auctions.decline("k-4", "-Ncancel1", "Not moved");
    expect(request).toHaveBeenLastCalledWith(
      "POST",
      "/staff/dealing/auctions/changes/-Ncancel1/decline",
      { note: "Not moved" },
      { idempotencyKey: "k-4" },
    );
    await auctions.withdraw("k-5", "-Ncancel1");
    expect(request).toHaveBeenLastCalledWith(
      "POST",
      "/staff/dealing/auctions/changes/-Ncancel1/withdraw",
      undefined,
      { idempotencyKey: "k-5" },
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
      toAuctionError(refused(422, "validation_failed", "note_required")).code,
    ).toBe("note_required");
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
