import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { RefundError, notConnectedCardRefundsApi, type CardRefundsApi } from "./api.ts";
import { CardRefunds } from "./CardRefunds.tsx";
import { createHttpCardRefundsApi, toRefundError } from "./http.ts";
import { createStubCardRefundsApi } from "./stub.ts";

const ME = "sample-me";
const NOW = new Date("2026-11-15T09:00:00Z");

function renderRefunds({
  api = createStubCardRefundsApi(ME, 0, "sample", () => NOW),
  canChange = true,
  address = "/reconciliation",
}: { api?: CardRefundsApi; canChange?: boolean; address?: string } = {}) {
  const keys: string[] = [];
  render(
    <MemoryRouter initialEntries={[address]}>
      <CardRefunds
        api={api}
        canChange={canChange}
        newKey={() => {
          keys.push(`key-${keys.length + 1}`);
          return keys[keys.length - 1] as string;
        }}
      />
    </MemoryRouter>,
  );
  return { api, keys, user: userEvent.setup() };
}

const card = (name: string) => screen.findByRole("article", { name });
const OTHERS = "Card refund, UGX 1,000,000";
const MINE = "Card refund, UGX 2,587,500";
const ASKED = "Card refund, UGX 600,000";
const listItem = (name: RegExp) => within(screen.getByRole("list", { name: "Card refunds" })).getByRole("button", { name });

describe("card payments to pay back", () => {
  it("opens the first refund waiting for a second person, with what goes back", async () => {
    renderRefunds();
    const refund = await card(OTHERS);
    expect(screen.getByText("2 waiting for a second person, 1 waiting on Pesapal.")).toBeInTheDocument();
    expect(within(refund).getByText("Visa ending 0010")).toBeInTheDocument();
    expect(within(refund).getByText(/card charge stays Finvest's income/)).toBeInTheDocument();
    expect(within(refund).getByRole("button", { name: "Approve and pay it back" })).toBeEnabled();
  });

  it("opens the refund the inbox links to", async () => {
    renderRefunds({ address: "/reconciliation?card_refund=-OdRefund0000000002" });
    const refund = await card(MINE);
    expect(within(refund).getByText(/Finvest's own mistake, so the card charge goes back too/)).toBeInTheDocument();
    expect(within(refund).getByRole("button", { name: "Withdraw the proposal" })).toBeInTheDocument();
    expect(within(refund).queryByRole("button", { name: "Approve and pay it back" })).not.toBeInTheDocument();
  });

  it("approves someone else's refund once, with one key, and shows Pesapal took it", async () => {
    const { user, keys } = renderRefunds();
    await user.click(within(await card(OTHERS)).getByRole("button", { name: "Approve and pay it back" }));
    const sent = await card(OTHERS);
    expect(within(sent).getByRole("status")).toHaveTextContent(/Pesapal took the request.*Pesapal still has to pay it/);
    expect(keys).toEqual(["key-1"]);
  });

  it("sends back only with a note", async () => {
    const { user } = renderRefunds();
    const refund = await card(OTHERS);
    await user.click(within(refund).getByRole("button", { name: "Send back" }));
    await user.click(within(refund).getByRole("button", { name: "Send it back" }));
    expect(within(refund).getByRole("alert")).toHaveTextContent("Say why");
    await user.type(within(refund).getByLabelText("Why you are sending it back"), "Check with the client first.");
    await user.click(within(refund).getByRole("button", { name: "Send it back" }));
    expect(within(await card(OTHERS)).getByRole("status")).toHaveTextContent(/Sent back.*Check with the client first\. Nothing moved\./);
  });

  it("records what Pesapal shows for a requested refund, and the worker marks it paid", async () => {
    const { user } = renderRefunds();
    await card(OTHERS);
    await user.click(listItem(/UGX 600,000/));
    const asked = await card(ASKED);
    const form = within(asked).getByRole("form", { name: "Say what Pesapal shows" });
    expect(within(form).queryByLabelText(/Pesapal has the request/)).not.toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Record it" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Choose what Pesapal shows.");
    await user.click(within(form).getByLabelText(/Pesapal paid it/));
    await user.type(within(form).getByLabelText("Where you saw it"), "Pesapal dashboard, 15 Nov");
    await user.click(within(form).getByRole("button", { name: "Record it" }));
    expect(within(await card(ASKED)).getByRole("status")).toHaveTextContent(/Paid back.*Pesapal dashboard, 15 Nov/);
  });

  it("proposes a refund with the charge when Finvest erred, and refuses one whose money has moved on", async () => {
    const { user } = renderRefunds();
    await card(OTHERS);
    await user.click(screen.getByRole("button", { name: "Propose a card refund" }));
    const form = screen.getByRole("form", { name: "Propose a card refund" });
    await user.type(within(form).getByLabelText("Card payment"), "-OdCardPay00000044");
    await user.type(within(form).getByLabelText("Why it goes back"), "Cancelled before it started.");
    await user.click(within(form).getByRole("button", { name: "Propose it" }));
    expect(await within(form).findByText(/no longer waiting for the investment/)).toBeInTheDocument();
  });

  it("shows only the refund to someone without the permission", async () => {
    renderRefunds({ canChange: false });
    const refund = await card(OTHERS);
    expect(within(refund).getByText(/You can see this refund/)).toBeInTheDocument();
    expect(within(refund).queryByRole("button", { name: "Approve and pay it back" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Propose a card refund" })).not.toBeInTheDocument();
  });

  it("says when there are none, and when they cannot be loaded", async () => {
    renderRefunds({ api: createStubCardRefundsApi(ME, 0, "empty") });
    expect(await screen.findByRole("heading", { name: "No card refunds" })).toBeInTheDocument();
  });

  it("offers to try again when the list cannot be loaded", async () => {
    renderRefunds({ api: notConnectedCardRefundsApi });
    expect(await screen.findByRole("alert")).toHaveTextContent("Card refunds are not connected to the Finvest service yet.");
  });

  it("reuses the key when an approval's answer is lost", async () => {
    const stub = createStubCardRefundsApi(ME, 0, "sample", () => NOW);
    const approve = vi.fn<CardRefundsApi["approve"]>().mockRejectedValueOnce(new RefundError("unavailable")).mockImplementation(stub.approve);
    const { user, keys } = renderRefunds({ api: { ...stub, approve } });
    const refund = await card(OTHERS);
    await user.click(within(refund).getByRole("button", { name: "Approve and pay it back" }));
    expect(await within(refund).findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    await user.click(within(refund).getByRole("button", { name: "Approve and pay it back" }));
    await within(await card(OTHERS)).findByText(/Pesapal took the request/);
    expect(approve.mock.calls.map((call) => call[0])).toEqual(["key-1", "key-1"]);
    expect(keys).toEqual(["key-1"]);
  });
});

describe("card refunds over the API", () => {
  const detail = {
    refund: {
      id: "r1",
      status: "proposed",
      deposit_id: "d1",
      client_id: "c1",
      investment_id: "i1",
      provider: "pesapal",
      amount: { minor: 1_000_000, currency: "UGX" },
      charge: { minor: 35_000, currency: "UGX" },
      finvest_erred: true,
      total: { minor: 1_035_000, currency: "UGX" },
      reason: "Our mistake",
      proposed_by: "s1",
      proposed_at: "2026-11-15T08:00:00Z",
      decided_by: null,
      decided_at: null,
      decision_note: null,
      sent_at: null,
      found: null,
      found_by: null,
      found_at: null,
      found_note: null,
      done_at: null,
      failure_code: null,
    },
    card_brand: "Visa",
    card_last_four: "0010",
    waiting: { minor: 1_000_000, currency: "UGX" },
    refusal: null,
    can_decide: true,
    can_record_finding: false,
    yours: false,
  };

  it("sends a finding with its key and reads the answer", async () => {
    const request = vi.fn().mockResolvedValue(detail);
    const api = createHttpCardRefundsApi({ request } as unknown as Http);
    const read = await api.recordFinding("k1", "r1", "paid", "Dashboard");
    expect(request).toHaveBeenCalledWith("POST", "/staff/payments/card-refunds/r1/finding", { found: "paid", note: "Dashboard" }, { idempotencyKey: "k1" });
    expect(read).toMatchObject({ id: "r1", finvestErred: true, cardLastFour: "0010", canDecide: true });
  });

  it("proposes with the charge flag, and turns an unknown payment into a field error", async () => {
    const request = vi.fn().mockRejectedValue(new ApiError("not_found", 404));
    const api = createHttpCardRefundsApi({ request } as unknown as Http);
    await expect(api.propose("k2", { depositId: "d9", reason: "x", finvestErred: true })).rejects.toMatchObject({ code: "deposit_not_found", field: "depositId" });
    expect(request).toHaveBeenCalledWith("POST", "/staff/payments/card-refunds", { deposit_id: "d9", reason: "x", finvest_erred: true }, { idempotencyKey: "k2" });
  });

  it("keeps a refusal's code and treats anything unknown as a lost answer", () => {
    const refused = new ApiError("validation_failed", 422, [{ field_path: ["body", "deposit_id"], code: "money_moved_on" }]);
    expect(toRefundError(refused)).toMatchObject({ code: "money_moved_on", field: "depositId" });
    expect(toRefundError(new ApiError("internal", 500))).toMatchObject({ code: "unavailable" });
    expect(toRefundError(new Error("x"))).toMatchObject({ code: "unavailable" });
  });
});
