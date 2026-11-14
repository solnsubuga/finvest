import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { CorrectionError, notConnectedMistakenPaymentsApi, type MistakenPaymentsApi } from "./api.ts";
import { createHttpMistakenPaymentsApi, toCorrectionError } from "./http.ts";
import { MistakenPayments } from "./MistakenPayments.tsx";
import { createStubMistakenPaymentsApi } from "./stub.ts";

const ME = "sample-me";
const NOW = new Date("2026-11-14T09:00:00Z");

function renderSteps({
  api = createStubMistakenPaymentsApi(ME, 0, "sample", () => NOW),
  canChange = true,
  address = "/reconciliation?reversal=-OdReversal00000001",
}: { api?: MistakenPaymentsApi; canChange?: boolean; address?: string } = {}) {
  const keys: string[] = [];
  render(
    <MemoryRouter initialEntries={[address]}>
      <MistakenPayments
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
const REVERSAL = "Take the payment back, UGX 2,500,000";
const listItem = (name: RegExp) => within(screen.getByRole("list", { name: "Mistaken payment steps" })).getByRole("button", { name });

describe("payments credited by mistake", () => {
  it("opens the reversal the inbox links to, with the payment and what still waits", async () => {
    renderSteps();
    const reversal = await card(REVERSAL);
    expect(screen.getByText("3 waiting for a second person.")).toBeInTheDocument();
    expect(within(reversal).getByText("MTN Mobile Money")).toBeInTheDocument();
    expect(within(reversal).getByText("-OdDeposit000000031")).toBeInTheDocument();
    expect(within(reversal).getByRole("button", { name: "Approve and take it back" })).toBeEnabled();
  });

  it("approves someone else's reversal once, with one key, and shows it taken back", async () => {
    const { user, keys } = renderSteps();
    await user.click(within(await card(REVERSAL)).getByRole("button", { name: "Approve and take it back" }));
    const done = await card(REVERSAL);
    expect(within(done).getByRole("status")).toHaveTextContent(/Taken back.*the client was told/);
    expect(within(done).queryByRole("button", { name: "Approve and take it back" })).not.toBeInTheDocument();
    expect(keys).toEqual(["key-1"]);
    expect(screen.getByText("2 waiting for a second person.")).toBeInTheDocument();
  });

  it("sends back only with a note", async () => {
    const { user } = renderSteps();
    const reversal = await card(REVERSAL);
    await user.click(within(reversal).getByRole("button", { name: "Send back" }));
    await user.click(within(reversal).getByRole("button", { name: "Send it back" }));
    expect(within(reversal).getByRole("alert")).toHaveTextContent("Say why");
    await user.type(within(reversal).getByLabelText("Why you are sending it back"), "MTN's report shows it arrived.");
    await user.click(within(reversal).getByRole("button", { name: "Send it back" }));
    const sent = await card(REVERSAL);
    expect(within(sent).getByRole("status")).toHaveTextContent("Sent back");
    expect(within(sent).getByRole("status")).toHaveTextContent("MTN's report shows it arrived.");
  });

  it("lets the proposer only withdraw their own", async () => {
    const { user } = renderSteps();
    await card(REVERSAL);
    await user.click(listItem(/UGX 800,000.*Needs a second person/));
    const mine = await card("Take the payment back, UGX 800,000");
    expect(within(mine).queryByRole("button", { name: "Approve and take it back" })).not.toBeInTheDocument();
    expect(within(mine).getByText(/You proposed this/)).toBeInTheDocument();
    await user.click(within(mine).getByRole("button", { name: "Withdraw the proposal" }));
    expect(within(await card("Take the payment back, UGX 800,000")).getByRole("status")).toHaveTextContent("Withdrawn");
  });

  it("opens a receivable step from its own link and records what the client owes", async () => {
    const { user } = renderSteps({ address: "/reconciliation?receivable=-OdOwed000000000001" });
    const step = await card("Record what the client owes, UGX 1,200,000");
    expect(within(step).getByText("The client owes now")).toBeInTheDocument();
    await user.click(within(step).getByRole("button", { name: "Approve and record it" }));
    expect(within(await card("Record what the client owes, UGX 1,200,000")).getByRole("status")).toHaveTextContent("Recorded");
    expect(within(screen.getByRole("list", { name: "Still owed" })).getAllByRole("listitem")).toHaveLength(2);
  });

  it("lists what is still owed, and starts writing one off from there", async () => {
    const { user } = renderSteps();
    await card(REVERSAL);
    const owed = screen.getByRole("list", { name: "Still owed" });
    expect(within(owed).getByText("UGX 450,000")).toBeInTheDocument();
    await user.click(within(owed).getByRole("button", { name: "Write it off" }));
    const form = screen.getByRole("form", { name: "Propose a step" });
    expect(within(form).getByLabelText("Payment")).toHaveValue("-OdDeposit000000028");
    expect(within(form).getByRole("radio", { name: /Write it off as a loss/ })).toBeChecked();
    await user.type(within(form).getByLabelText("What happened"), "Airtel will not return it.");
    await user.click(within(form).getByRole("button", { name: "Propose it" }));
    await card("Write it off as a loss, UGX 450,000");
    expect(within(await card("Write it off as a loss, UGX 450,000")).getByText(/You proposed this/)).toBeInTheDocument();
  });

  it("proposes a step and says what the service refused", async () => {
    const { user } = renderSteps();
    await card(REVERSAL);
    await user.click(screen.getByRole("button", { name: "Propose a step" }));
    const form = screen.getByRole("form", { name: "Propose a step" });
    await user.click(within(form).getByRole("button", { name: "Propose it" }));
    expect(within(form).getByText(/Enter the ID of the payment/)).toBeInTheDocument();

    await user.click(within(form).getByRole("radio", { name: /Record what the client owes/ }));
    await user.type(within(form).getByLabelText("Payment"), "-OdDeposit000000033");
    await user.type(within(form).getByLabelText("What happened"), "The bank recalled it.");
    await user.click(within(form).getByRole("button", { name: "Propose it" }));
    expect(await within(form).findByText(/The investment is still running/)).toBeInTheDocument();

    await user.clear(within(form).getByLabelText("Payment"));
    await user.type(within(form).getByLabelText("Payment"), "-OdNothing");
    await user.click(within(form).getByRole("button", { name: "Propose it" }));
    expect(await within(form).findByText("No payment has that ID.")).toBeInTheDocument();
  });

  it("shows a step without actions to someone who can only see", async () => {
    renderSteps({ canChange: false });
    const reversal = await card(REVERSAL);
    expect(within(reversal).getByText(/You can see this step/)).toBeInTheDocument();
    expect(within(reversal).queryByRole("button", { name: "Approve and take it back" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Propose a step" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Write it off" })).not.toBeInTheDocument();
  });

  it("says when there are none", async () => {
    renderSteps({ api: createStubMistakenPaymentsApi(ME, 0, "empty", () => NOW), address: "/reconciliation" });
    expect(await screen.findByText("No mistaken payments")).toBeInTheDocument();
  });

  it("says when the service cannot be reached", async () => {
    renderSteps({ api: createStubMistakenPaymentsApi(ME, 0, "error", () => NOW) });
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says when it is not connected", async () => {
    renderSteps({ api: notConnectedMistakenPaymentsApi });
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected");
  });

  it("keeps the key after an answer that never arrived", async () => {
    const stub = createStubMistakenPaymentsApi(ME, 0, "sample", () => NOW);
    const approve = vi.fn(stub.approve);
    approve.mockRejectedValueOnce(new CorrectionError("unavailable"));
    const { user, keys } = renderSteps({ api: { ...stub, approve } });
    const reversal = await card(REVERSAL);
    await user.click(within(reversal).getByRole("button", { name: "Approve and take it back" }));
    expect(await within(reversal).findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    await user.click(within(reversal).getByRole("button", { name: "Approve and take it back" }));
    await card(REVERSAL);
    expect(approve.mock.calls.map((call) => call[0])).toEqual(["key-1", "key-1"]);
    expect(keys).toEqual(["key-1"]);
  });
});

describe("the mistaken payments HTTP client", () => {
  const common = {
    deposit_id: "d1",
    client_id: "c1",
    amount: { minor: 900_000, currency: "UGX" },
    reason: "why",
    proposed_at: "2026-11-14T08:00:00Z",
    decided_at: null,
    decision_note: null,
  };

  it("lists reversals and receivable steps together, newest first", async () => {
    const request = vi.fn((_method: string, path: string) =>
      Promise.resolve(
        path === "/staff/payments/reversals"
          ? [{ ...common, id: "r1", status: "failed", investment_id: "i1", failure_code: "money_moved_on", proposed_at: "2026-11-14T07:00:00Z" }]
          : [{ ...common, id: "s1", kind: "write_off", status: "proposed" }],
      ),
    );
    const http = { request, download: vi.fn() } as unknown as Http;
    const items = await createHttpMistakenPaymentsApi(http).list();
    expect(items.map((item) => [item.id, item.step, item.status])).toEqual([
      ["s1", "write_off", "proposed"],
      ["r1", "reverse", "failed"],
    ]);
    expect(items[1]).toMatchObject({ investmentId: "i1", failureCode: "money_moved_on" });
  });

  it("sends each command to its own route with the idempotency key", async () => {
    const request = vi.fn().mockResolvedValue({
      step: { ...common, id: "s1", kind: "open", status: "proposed" },
      owed: { minor: 0, currency: "UGX" },
      method: "bank_transfer",
      refusal: null,
      can_decide: false,
      yours: true,
    });
    const api = createHttpMistakenPaymentsApi({ request, download: vi.fn() } as unknown as Http);
    const made = await api.propose("k-1", { step: "open", depositId: "d1", reason: "why" });
    expect(request).toHaveBeenCalledWith("POST", "/staff/payments/receivables", { kind: "open", deposit_id: "d1", reason: "why" }, { idempotencyKey: "k-1" });
    expect(made).toMatchObject({ id: "s1", step: "open", method: "bank_transfer", yours: true, balance: { minor: 0 } });

    request.mockResolvedValue({
      reversal: { ...common, id: "r1", status: "approved", investment_id: "i1", failure_code: null },
      waiting: { minor: 900_000, currency: "UGX" },
      method: "mtn",
      refusal: null,
      can_decide: false,
      yours: false,
    });
    const approved = await api.approve("k-2", "reversal", "r1");
    expect(request).toHaveBeenLastCalledWith("POST", "/staff/payments/reversals/r1/approve", undefined, { idempotencyKey: "k-2" });
    expect(approved).toMatchObject({ step: "reverse", status: "approved", balance: { minor: 900_000 } });
  });

  it("turns the service's refusals into the screen's, with the field they are about", () => {
    const refused = (code: string, field: string) =>
      toCorrectionError(new ApiError("validation_failed", 422, [{ field_path: ["body", field], code }]));
    expect(refused("unwind_first", "deposit_id")).toMatchObject({ code: "unwind_first", field: "depositId" });
    expect(refused("insufficient_funds", "deposit_id")).toMatchObject({ code: "insufficient_funds", field: "depositId" });
    expect(refused("too_long", "reason")).toMatchObject({ code: "invalid", field: "reason" });
    expect(toCorrectionError(new ApiError("confirm_it_is_you", 403)).code).toBe("confirm_it_is_you");
    expect(toCorrectionError(new ApiError("idempotency_key_reused", 422)).code).toBe("conflict");
    expect(toCorrectionError(new ApiError("internal_error", 500)).code).toBe("unavailable");
    expect(toCorrectionError(new TypeError("offline")).code).toBe("unavailable");
  });
});
