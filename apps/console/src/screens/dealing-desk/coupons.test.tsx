import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { CONFIRM_IT_IS_YOU } from "@/api/confirmation.ts";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { CouponsSection } from "./Coupons.tsx";
import { CouponError, notConnectedCouponsApi, type CouponsApi } from "./coupons.ts";
import { createHttpCouponsApi, toCouponError, type CouponsJson } from "./couponsHttp.ts";
import { COUPON_COLLEAGUES, createStubCouponsApi } from "./couponsStub.ts";

const ME = "-Nstaffme00000000000";
const TODAY = "2026-11-17";
const NOW = () => new Date("2026-11-17T10:00:00Z");
const colleagues = Object.values(COUPON_COLLEAGUES);
const names: Directory = { ...emptyDirectory, staffName: (id) => Promise.resolve(colleagues.find((c) => c.id === id)?.name ?? null) };
const ugx = (minor: number) => ({ minor, currency: "UGX" as const });

function stub(options: Parameters<typeof createStubCouponsApi>[1] = {}) {
  return createStubCouponsApi({ id: ME }, { delayMs: 0, today: () => TODAY, now: NOW, ...options });
}

function renderCoupons(api: CouponsApi = stub()) {
  let keys = 0;
  render(<CouponsSection api={api} directory={names} me={ME} newKey={() => `key-${++keys}`} today={() => TODAY} />);
  return { api, user: userEvent.setup() };
}

const due = () => screen.findByRole("list", { name: "Coupons due to record" });
const recorded = (name: string) => screen.findByRole("listitem", { name });

async function openForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(within(await due()).getByRole("button", { name: "Record what arrived" }));
  return screen.getByRole("form", { name: "Record the coupon" });
}

describe("coupons on the desk's bonds", () => {
  it("shows what is due, and what is recorded with who recorded and decided it", async () => {
    renderCoupons();
    const list = await due();
    expect(within(list).getByText("2 Year Treasury Bond 2028, coupon of 16 Nov 2026")).toBeInTheDocument();
    expect(within(list).getByText("UGX 150,000,000")).toBeInTheDocument();
    expect(within(list).getByText("UGX 11,437,500")).toBeInTheDocument();
    const waiting = await recorded("BOND-10Y-2037 coupon of 11 Aug 2026");
    expect(within(waiting).getByText("Waiting for a second person")).toBeInTheDocument();
    expect(await within(waiting).findByText(/^Daniel Okello, /)).toBeInTheDocument();
    const approved = await recorded("BOND-5Y-2032 coupon of 20 May 2026");
    expect(within(approved).getByText("UGX 5,850,000 on 21 May 2026")).toBeInTheDocument();
    expect(within(approved).getByText(/The custodian took its fee/)).toBeInTheDocument();
    expect(await within(approved).findByText("Grace Atim")).toBeInTheDocument();
  });

  it("records a coupon that arrived as expected, and leaves it for someone else to approve", async () => {
    const { user, api } = renderCoupons();
    const record = vi.spyOn(api, "record");
    const form = await openForm(user);
    expect(within(form).getByLabelText("What arrived (UGX)")).toHaveValue("11437500");
    await user.type(within(form).getByLabelText("Bank reference"), "FT26320CPN28");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(record).toHaveBeenCalledWith("key-1", {
      issueCode: "BOND-2Y-2028",
      couponDate: "2026-11-16",
      amount: ugx(11_437_500),
      valueDate: TODAY,
      bankReference: "FT26320CPN28",
      note: null,
    });
    const mine = await recorded("BOND-2Y-2028 coupon of 16 Nov 2026");
    expect(within(mine).getByText("You recorded this, so someone else has to approve it.")).toBeInTheDocument();
    expect(within(mine).queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();
    expect(screen.getByText(/Every coupon the desk's bonds have earned so far is recorded/)).toBeInTheDocument();
  });

  it("asks why when what arrived differs, and checks the day it arrived", async () => {
    const { user, api } = renderCoupons();
    const record = vi.spyOn(api, "record");
    const form = await openForm(user);
    const amount = within(form).getByLabelText("What arrived (UGX)");
    await user.clear(amount);
    await user.type(amount, "11,300,000");
    const day = within(form).getByLabelText("Arrived on");
    await user.clear(day);
    await user.type(day, "2026-11-15");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getByText("What arrived differs from what the bonds earn. Say why.")).toBeInTheDocument();
    expect(within(form).getByText("The money arrives on or after the coupon date, 16 Nov 2026.")).toBeInTheDocument();
    expect(within(form).getByText("Enter the bank's reference for the payment.")).toBeInTheDocument();
    expect(within(form).getByText("The bonds earn UGX 11,437,500 before any tax.")).toBeInTheDocument();
    expect(record).not.toHaveBeenCalled();
  });

  it("approves a coupon someone else recorded after asking about the bank statement", async () => {
    const { user } = renderCoupons();
    const waiting = await recorded("BOND-10Y-2037 coupon of 11 Aug 2026");
    await user.click(within(waiting).getByRole("button", { name: "Approve as second person" }));
    expect(within(waiting).getByText("Does the trust account statement show UGX 4,262,500 arriving on 12 Aug 2026 with reference FT26224CPN37?")).toBeInTheDocument();
    await user.click(within(waiting).getByRole("button", { name: "Yes, approve and post" }));
    const approved = await recorded("BOND-10Y-2037 coupon of 11 Aug 2026");
    expect(await within(approved).findByText("Approved and posted")).toBeInTheDocument();
    expect(within(approved).getByText("You")).toBeInTheDocument();
  });

  it("declines with a reason, and the coupon is due again", async () => {
    const { user } = renderCoupons();
    const waiting = await recorded("BOND-10Y-2037 coupon of 11 Aug 2026");
    await user.click(within(waiting).getByRole("button", { name: "Decline" }));
    await user.click(within(waiting).getByRole("button", { name: "Decline" }));
    expect(within(waiting).getByText(/Write a short note/)).toBeInTheDocument();
    await user.type(within(waiting).getByLabelText("Why are you declining it?"), "The statement shows FT26224CPN73");
    await user.click(within(waiting).getByRole("button", { name: "Decline" }));
    const declined = await recorded("BOND-10Y-2037 coupon of 11 Aug 2026");
    expect(await within(declined).findByText("Declined")).toBeInTheDocument();
    expect(within(declined).getByText("The statement shows FT26224CPN73")).toBeInTheDocument();
    expect(within(await due()).getByText("10 Year Treasury Bond 2037, coupon of 11 Aug 2026")).toBeInTheDocument();
  });

  it("sends the same key again when the first approval got no answer", async () => {
    const api = stub();
    const approve = vi.spyOn(api, "approve").mockRejectedValueOnce(new CouponError("unavailable"));
    const { user } = renderCoupons(api);
    const waiting = await recorded("BOND-10Y-2037 coupon of 11 Aug 2026");
    await user.click(within(waiting).getByRole("button", { name: "Approve as second person" }));
    await user.click(within(waiting).getByRole("button", { name: "Yes, approve and post" }));
    expect(await within(waiting).findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    await user.click(within(waiting).getByRole("button", { name: "Yes, approve and post" }));
    expect(approve.mock.calls.map((call) => call[0])).toEqual(["key-1", "key-1"]);
  });

  it("only lets people who may change the desk record coupons", async () => {
    renderCoupons(stub({ mayChange: false }));
    await due();
    expect(screen.queryByRole("button", { name: "Record what arrived" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();
  });

  it("says when nothing is due or recorded", async () => {
    renderCoupons(stub({ sample: "empty" }));
    expect(await screen.findByText(/Every coupon the desk's bonds have earned so far is recorded/)).toBeInTheDocument();
    expect(screen.getByText("No coupon has been recorded yet.")).toBeInTheDocument();
  });

  it("says when coupons cannot be loaded, and offers to try again", async () => {
    renderCoupons(notConnectedCouponsApi);
    expect(await screen.findByRole("heading", { name: "Coupons could not be loaded" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

const json: CouponsJson = {
  currency: "UGX",
  may_change: true,
  due: [{ issue_code: "BOND-2Y-2028", name: "2 Year Treasury Bond 2028", coupon_date: "2026-11-16", face: ugx(150_000_000), expected: ugx(11_437_500) }],
  coupons: [
    {
      id: "-Ncoupon1",
      issue_code: "BOND-5Y-2032",
      coupon_date: "2026-05-20",
      face: ugx(80_000_000),
      expected: ugx(6_000_000),
      amount: ugx(5_850_000),
      value_date: "2026-05-21",
      bank_reference: "FT1",
      note: "Fee",
      status: "recorded",
      recorded_by: "-Nstaff1",
      recorded_at: "2026-05-21T09:10:00Z",
      decided_by: null,
      decided_at: null,
      decision_reason: null,
      allowed: ["approve", "decline"],
    },
  ],
};

describe("coupons over the API", () => {
  function http() {
    const request = vi.fn().mockResolvedValue(json);
    return { request, api: createHttpCouponsApi({ request, download: vi.fn() } as unknown as Http) };
  }

  it("reads the screen for a currency", async () => {
    const { request, api } = http();
    const screen = await api.getCoupons("UGX");
    expect(request).toHaveBeenCalledWith("GET", "/staff/dealing/bond-coupons?currency=UGX");
    expect(screen.mayChange).toBe(true);
    expect(screen.due[0]).toEqual({ issueCode: "BOND-2Y-2028", name: "2 Year Treasury Bond 2028", couponDate: "2026-11-16", face: ugx(150_000_000), expected: ugx(11_437_500) });
    expect(screen.coupons[0]).toMatchObject({ issueCode: "BOND-5Y-2032", amount: ugx(5_850_000), valueDate: "2026-05-21", recordedBy: "-Nstaff1", allowed: ["approve", "decline"] });
  });

  it("sends each command to its route with its key", async () => {
    const { request, api } = http();
    await api.record("k-1", { issueCode: "BOND-2Y-2028", couponDate: "2026-11-16", amount: ugx(11_437_500), valueDate: "2026-11-17", bankReference: "FT2", note: null });
    await api.approve("k-2", "c 1");
    await api.decline("k-3", "c1", "Wrong reference");
    expect(request.mock.calls.map((call) => [call[1], call[2], call[3]])).toEqual([
      [
        "/staff/dealing/bond-coupons",
        { issue_code: "BOND-2Y-2028", coupon_date: "2026-11-16", currency: "UGX", amount_minor: 11_437_500, value_date: "2026-11-17", bank_reference: "FT2", note: null },
        { idempotencyKey: "k-1" },
      ],
      ["/staff/dealing/bond-coupons/c%201/approve", undefined, { idempotencyKey: "k-2" }],
      ["/staff/dealing/bond-coupons/c1/decline", { reason: "Wrong reference" }, { idempotencyKey: "k-3" }],
    ]);
  });

  it("reads the backend's refusals as the screen's own", () => {
    const field = (code: string) => new ApiError("validation_failed", 422, [{ field_path: ["value_date"], code }]);
    expect(toCouponError(field("value_date_before_coupon")).code).toBe("value_date_before_coupon");
    expect(toCouponError(field("value_date_in_a_closed_day")).code).toBe("value_date_in_a_closed_day");
    expect(toCouponError(field("note_required")).code).toBe("note_required");
    expect(toCouponError(field("already_recorded")).code).toBe("already_recorded");
    expect(toCouponError(field("nothing_held")).code).toBe("nothing_held");
    expect(toCouponError(field("something_new")).code).toBe("invalid");
    expect(toCouponError(new ApiError(CONFIRM_IT_IS_YOU, 403)).code).toBe("not_confirmed");
    expect(toCouponError(new ApiError("conflict", 409)).code).toBe("conflict");
    expect(toCouponError(new TypeError("x")).code).toBe("unavailable");
  });
});
