import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { ReassignmentError, notConnectedReassignmentsApi, type ReassignmentsApi } from "./api.ts";
import { createHttpReassignmentsApi, toReassignmentError } from "./http.ts";
import { Reassignments } from "./Reassignments.tsx";
import { createStubReassignmentsApi } from "./stub.ts";

const ME = "sample-me";
const NOW = new Date("2026-11-13T09:00:00Z");

function renderMoves({
  api = createStubReassignmentsApi(ME, 0, "sample", () => NOW),
  canChange = true,
  address = "/reconciliation",
}: { api?: ReassignmentsApi; canChange?: boolean; address?: string } = {}) {
  const keys: string[] = [];
  render(
    <MemoryRouter initialEntries={[address]}>
      <Reassignments
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

const move = (amount: string) => screen.findByRole("article", { name: `Move of ${amount}` });
const listItem = (name: RegExp) => within(screen.getByRole("list", { name: "Moves" })).getByRole("button", { name });

describe("payments credited to the wrong client", () => {
  it("opens the proposal waiting for this person, with both clients and the statement line", async () => {
    renderMoves();
    const card = await move("UGX 4,000,000");
    expect(screen.getByText("2 waiting for a second person.")).toBeInTheDocument();
    expect(within(card).getByText("Sarah Namukasa")).toBeInTheDocument();
    expect(within(card).getByText("Peter Okello")).toBeInTheDocument();
    expect(within(card).getByText("RTGS IN PETER OKELLO")).toBeInTheDocument();
    expect(within(card).getByText("The reference carries Peter Okello's account number.")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Approve and move the money" })).toBeEnabled();
  });

  it("approves someone else's proposal once, with one key, and shows it moved", async () => {
    const { user, keys } = renderMoves();
    const card = await move("UGX 4,000,000");
    await user.click(within(card).getByRole("button", { name: "Approve and move the money" }));
    const moved = await move("UGX 4,000,000");
    expect(within(moved).getByRole("status")).toHaveTextContent(/Moved .*Peter Okello's payment for their investment/);
    expect(within(moved).queryByRole("button", { name: "Approve and move the money" })).not.toBeInTheDocument();
    expect(keys).toEqual(["key-1"]);
    expect(screen.getByText("1 waiting for a second person.")).toBeInTheDocument();
  });

  it("sends back only with a note", async () => {
    const { user } = renderMoves();
    const card = await move("UGX 4,000,000");
    await user.click(within(card).getByRole("button", { name: "Send back" }));
    await user.click(within(card).getByRole("button", { name: "Send it back" }));
    expect(within(card).getByRole("alert")).toHaveTextContent("Say why");
    await user.type(within(card).getByLabelText("Why you are sending it back"), "The receipts do not agree.");
    await user.click(within(card).getByRole("button", { name: "Send it back" }));
    const sent = await move("UGX 4,000,000");
    expect(within(sent).getByRole("status")).toHaveTextContent("Sent back");
    expect(within(sent).getByRole("status")).toHaveTextContent("The receipts do not agree.");
  });

  it("lets the proposer only withdraw their own, and warns when it cannot be approved", async () => {
    const { user } = renderMoves();
    await move("UGX 4,000,000");
    await user.click(listItem(/UGX 1,500,000.*Needs a second person/));
    const mine = await move("UGX 1,500,000");
    expect(within(mine).queryByRole("button", { name: "Approve and move the money" })).not.toBeInTheDocument();
    expect(within(mine).getByText(/You proposed this/)).toBeInTheDocument();
    await user.click(within(mine).getByRole("button", { name: "Withdraw the proposal" }));
    expect(within(await move("UGX 1,500,000")).getByRole("status")).toHaveTextContent("Withdrawn");
  });

  it("shows a decided move without actions to someone who can only see", async () => {
    renderMoves({ canChange: false, address: "/reconciliation?reassignment=-OdMove0000000000061" });
    const card = await move("UGX 4,000,000");
    expect(within(card).getByText(/You can see this move/)).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Approve and move the money" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Propose a move" })).not.toBeInTheDocument();
  });

  it("proposes a move and says which field the service refused", async () => {
    const { user } = renderMoves();
    await move("UGX 4,000,000");
    await user.click(screen.getByRole("button", { name: "Propose a move" }));
    const form = screen.getByRole("form", { name: "Propose a move" });
    await user.click(within(form).getByRole("button", { name: "Propose the move" }));
    expect(within(form).getByText(/Enter the ID of the payment/)).toBeInTheDocument();

    await user.type(within(form).getByLabelText("Payment credited to the wrong client"), "-OdMomo000000000023A");
    await user.type(within(form).getByLabelText("The right client's investment"), "-OdInv00000000000053");
    await user.type(within(form).getByLabelText("How you know whose money it is"), "The sender is another client.");
    await user.click(within(form).getByRole("button", { name: "Propose the move" }));
    expect(await within(form).findByText(/This is a mobile money payment/)).toBeInTheDocument();

    await user.clear(within(form).getByLabelText("Payment credited to the wrong client"));
    await user.type(within(form).getByLabelText("Payment credited to the wrong client"), "-OdBank000000000022A");
    await user.click(within(form).getByRole("button", { name: "Propose the move" }));
    expect(await within(form).findByText(/That investment belongs to the same client|already has a payment|different amount/)).toBeInTheDocument();
  });

  it("shows nothing moved yet, and a failure that can be tried again", async () => {
    renderMoves({ api: createStubReassignmentsApi(ME, 0, "empty", () => NOW) });
    expect(await screen.findByText("No payments have been moved")).toBeInTheDocument();
  });

  it("says when the service cannot be reached", async () => {
    renderMoves({ api: createStubReassignmentsApi(ME, 0, "error", () => NOW) });
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says when it is not connected", async () => {
    renderMoves({ api: notConnectedReassignmentsApi });
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected");
  });

  it("keeps the key after an answer that never arrived", async () => {
    const stub = createStubReassignmentsApi(ME, 0, "sample", () => NOW);
    const approve = vi.fn(stub.approve);
    approve.mockRejectedValueOnce(new ReassignmentError("unavailable"));
    const { user, keys } = renderMoves({ api: { ...stub, approve } });
    const card = await move("UGX 4,000,000");
    await user.click(within(card).getByRole("button", { name: "Approve and move the money" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    await user.click(within(card).getByRole("button", { name: "Approve and move the money" }));
    await move("UGX 4,000,000");
    expect(approve.mock.calls.map((call) => call[0])).toEqual(["key-1", "key-1"]);
    expect(keys).toEqual(["key-1"]);
  });
});

describe("the reassignments HTTP client", () => {
  it("calls the staff routes with the idempotency key and maps the answer", async () => {
    const request = vi.fn().mockResolvedValue({
      reassignment: {
        id: "r1",
        status: "proposed",
        deposit_id: "d1",
        from_investment_id: "i1",
        to_investment_id: "i2",
        amount: { minor: 4_000_000, currency: "UGX" },
        reason: "why",
        proposed_at: "2026-11-13T08:00:00Z",
        decided_at: null,
        decision_note: null,
        new_deposit_id: null,
      },
      from_client: { client_id: "c1", name: "A", account_number: "1" },
      to_client: { client_id: "c2", name: "B", account_number: "2" },
      line: null,
      reference_names_to_client: false,
      reference_names_from_client: false,
      refusal: "money_moved_on",
      can_decide: true,
      yours: false,
    });
    const http: Http = { request, download: vi.fn() };
    const api = createHttpReassignmentsApi(http);
    const detail = await api.approve("k-1", "r1");
    expect(request).toHaveBeenCalledWith("POST", "/staff/payments/reassignments/r1/approve", undefined, { idempotencyKey: "k-1" });
    expect(detail).toMatchObject({ id: "r1", depositId: "d1", toClient: { name: "B" }, refusal: "money_moved_on", canDecide: true });
  });

  it("turns the service's refusals into the screen's, with the field they are about", () => {
    const refused = (code: string, field: string) =>
      toReassignmentError(new ApiError("validation_failed", 422, [{ field_path: ["body", field], code }]));
    expect(refused("money_moved_on", "deposit_id")).toMatchObject({ code: "money_moved_on", field: "depositId" });
    expect(refused("same_client", "to_investment_id")).toMatchObject({ code: "same_client", field: "toInvestmentId" });
    expect(refused("not_found", "to_investment_id")).toMatchObject({ code: "investment_not_found", field: "toInvestmentId" });
    expect(toReassignmentError(new ApiError("confirm_it_is_you", 403)).code).toBe("confirm_it_is_you");
    // A key spent on another request is a definite refusal, so the next try gets a new key.
    expect(toReassignmentError(new ApiError("idempotency_key_reused", 422)).code).toBe("conflict");
    expect(toReassignmentError(new ApiError("internal_error", 500)).code).toBe("unavailable");
    expect(toReassignmentError(new TypeError("offline")).code).toBe("unavailable");
  });
});
