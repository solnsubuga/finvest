import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { ActionsSection } from "./Actions.tsx";
import { ACTION_KINDS, ActionError, createHttpActionsApi, notConnectedActionsApi, type ActionKind, type ActionsApi } from "./actions.ts";
import { createStubActionsApi } from "./actionsStub.ts";

const ME = { id: "stub-operations", name: "Opio Peter" };
const NOW = Date.parse("2026-11-14T09:00:00Z");
const CLIENT = "-NstubClient00000042";

function renderActions(api: ActionsApi = createStubActionsApi(ME, ACTION_KINDS, "sample", () => NOW, 0), canRefund = true) {
  render(
    <MemoryRouter>
      <ActionsSection api={api} clientId={CLIENT} canRefund={canRefund} />
    </MemoryRouter>,
  );
  return userEvent.setup();
}

describe("acting for a client", () => {
  it("approves a colleague's PIN reset with a note for the audit trail", async () => {
    const user = renderActions();
    const card = await screen.findByRole("article", { name: "Reset the app PIN, waiting" });
    expect(within(card).getByText(/Proposed by Grace Auma/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Approve and sign out of the app" }));
    await user.click(within(card).getByRole("button", { name: "Approve and sign out of the app" }));
    expect(within(card).getByText("Write a short reason for the audit trail.")).toBeInTheDocument();
    await user.type(within(card).getByLabelText(/Note for the audit trail/), "Called the client back on their number");
    await user.click(within(card).getByRole("button", { name: "Approve and sign out of the app" }));
    expect(await screen.findByRole("status")).toHaveTextContent("signed out of the phone app");
    // The notice shows as soon as the approval answers; the list is fetched again after it.
    await waitFor(() => expect(screen.queryByRole("article")).not.toBeInTheDocument());
    expect(within(screen.getByRole("list", { name: "Decided actions, newest first" })).getAllByRole("listitem")).toHaveLength(2);
  });

  it("offers only what is not already waiting, and only to roles that may", async () => {
    renderActions(createStubActionsApi(ME, ["pin_reset", "statement"], "sample", () => NOW, 0));
    await screen.findByRole("article", { name: "Reset the app PIN, waiting" });
    expect(screen.getByRole("button", { name: "Send a statement" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset app PIN" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start a withdrawal" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change withdrawal limits" })).not.toBeInTheDocument();
  });

  it("lets someone who cannot decide see what waits, but not decide it", async () => {
    renderActions(createStubActionsApi(ME, [], "sample", () => NOW, 0), false);
    const card = await screen.findByRole("article", { name: "Reset the app PIN, waiting" });
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText(/Your role cannot propose anything/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Investments" })).not.toBeInTheDocument();
  });

  it("proposes stricter limits, refusing ones that are not lower than the shared limits", async () => {
    const user = renderActions();
    await user.click(await screen.findByRole("button", { name: "Change withdrawal limits" }));
    const form = screen.getByRole("form", { name: "Change withdrawal limits" });
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getByText("Set at least one limit.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText(/Mobile money, one payout/), "6,000,000");
    await user.type(within(form).getByLabelText(/Why/), "Client asked for a lower limit after a scam attempt");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getByText(/Must be lower than the shared UGX 5,000,000/)).toBeInTheDocument();
    await user.clear(within(form).getByLabelText(/Mobile money, one payout/));
    await user.type(within(form).getByLabelText(/Mobile money, one payout/), "500,000");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const card = await screen.findByRole("article", { name: "Change withdrawal limits, waiting" });
    expect(within(card).getByText(/UGX 500,000/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
  });

  it("starts a withdrawal only from the client's own written request", async () => {
    const user = renderActions();
    await user.click(await screen.findByRole("button", { name: "Start a withdrawal" }));
    const form = screen.getByRole("form", { name: "Start a withdrawal" });
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getAllByText("Choose one.")).toHaveLength(3);
    await user.click(within(form).getByRole("radio", { name: /school fees/ }));
    await user.click(within(form).getByRole("radio", { name: /182-day Treasury bill/ }));
    await user.click(within(form).getByRole("radio", { name: /^MTN/ }));
    await user.type(within(form).getByLabelText(/Why/), "Called the client back, they confirmed");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const card = await screen.findByRole("article", { name: "Start a withdrawal, waiting" });
    expect(within(card).getByText(/182-day Treasury bill, about UGX 2,084,500 today, paid to MTN/)).toBeInTheDocument();
    expect(within(card).getByText(/The client wrote/)).toHaveTextContent("school fees");
  });

  it("says why a withdrawal cannot be started when the client has not asked", async () => {
    const user = renderActions(createStubActionsApi(ME, ACTION_KINDS, "empty", () => NOW, 0));
    await user.click(await screen.findByRole("button", { name: "Start a withdrawal" }));
    expect(screen.getByText(/Early withdrawal is switched off/)).toBeInTheDocument();
    expect(screen.getByText("Nothing has been proposed for this client before.")).toBeInTheDocument();
  });

  it("proposes a statement for the days chosen", async () => {
    const user = renderActions();
    await user.click(await screen.findByRole("button", { name: "Send a statement" }));
    const form = screen.getByRole("form", { name: "Send a statement" });
    await user.type(within(form).getByLabelText("From"), "2026-09-01");
    await user.clear(within(form).getByLabelText("To"));
    await user.type(within(form).getByLabelText("To"), "2026-10-31");
    await user.type(within(form).getByLabelText(/Why/), "Client asked by phone for their loan application");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const card = await screen.findByRole("article", { name: "Send a statement, waiting" });
    expect(within(card).getByText(/From 1 Sept 2026 to 31 Oct 2026|From .*2026 to .*2026/)).toBeInTheDocument();
  });

  it("sends the same key again when a proposal is retried, and a new one for the next", async () => {
    const stub = createStubActionsApi(ME, ACTION_KINDS, "empty", () => NOW, 0);
    const keys: string[] = [];
    let fail = true;
    const api: ActionsApi = {
      ...stub,
      propose: (clientId, proposal, key) => {
        keys.push(key);
        if (fail) {
          fail = false;
          return Promise.reject(new ActionError("unavailable"));
        }
        return stub.propose(clientId, proposal, key);
      },
    };
    let next = 0;
    render(
      <MemoryRouter>
        <ActionsSection api={api} clientId={CLIENT} canRefund newKey={() => `key-${String(++next)}`} />
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Reset app PIN" }));
    await user.type(screen.getByLabelText(/Why/), "Client called from their number");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByText(/could not reach Finvest/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    await screen.findByRole("article", { name: "Reset the app PIN, waiting" });
    expect(keys).toEqual(["key-1", "key-1"]);
  });

  it("shows when acting for a client is not connected", async () => {
    renderActions(notConnectedActionsApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected to the Finvest service yet");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

function reply(status: number, body?: unknown) {
  return Promise.resolve(
    body === undefined ? new Response(null, { status }) : new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
}

function refusal(status: number, code: string, fields: unknown[] = []) {
  return reply(status, { request_id: "r", error: { code, message: code, fields } });
}

const ACTION = {
  id: "-Naction00000000001",
  client_id: CLIENT,
  kind: "withdrawal_limits" as ActionKind,
  reason: "Scam attempt",
  proposed_by: "-Nstaff0000000GRACE",
  proposed_by_name: "Grace Auma",
  proposed_at: "2026-11-14T07:00:00Z",
  status: "proposed",
  decided_by: null,
  decided_by_name: null,
  decided_at: null,
  decision_reason: null,
  limits: { mobile_money_per_payout: 500000, mobile_money_per_day: null, bank_per_payout: null, bank_per_day: null, review_above: null },
  statement: null,
  withdrawal: null,
  yours: false,
};

describe("client actions over HTTP", () => {
  const apiWith = (fetch: typeof globalThis.fetch) => createHttpActionsApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));

  it("reads the actions and the limits in the console's shape", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) =>
      String(url).endsWith("/actions")
        ? reply(200, { actions: [ACTION], allowed: ["withdrawal_limits"] })
        : reply(200, { shared: ACTION.limits, own: null, applied: ACTION.limits, can_change: true }),
    );
    const api = apiWith(fetch);
    const found = await api.actionsFor(CLIENT);
    expect(found.allowed).toEqual(["withdrawal_limits"]);
    expect(found.actions[0]).toMatchObject({ kind: "withdrawal_limits", proposedByName: "Grace Auma", limits: { mobileMoneyPerPayout: 500000, bankPerDay: null } });
    expect((await api.limitsFor(CLIENT)).own).toBeNull();
    expect(fetch.mock.calls.map(([url]) => String(url))).toEqual([
      `https://api.test/staff/clients/${CLIENT}/actions`,
      `https://api.test/staff/clients/${CLIENT}/limits`,
    ]);
  });

  it("sends each kind's details under its own name, and decides on the decision path", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(201, ACTION));
    const api = apiWith(fetch);
    await api.propose(CLIENT, { kind: "pin_reset", reason: "Forgot PIN" }, "key-propose-1");
    await api.propose(CLIENT, { kind: "statement", reason: "Loan", statement: { from: "2026-09-01", to: "2026-10-31" } }, "key-propose-2");
    await api.propose(CLIENT, { kind: "withdrawal", reason: "Asked", withdrawal: { investmentId: "-Ni", destinationId: "-Nd", messageId: "-Nm" } }, "key-propose-3");
    await api.decide(ACTION.id, "approved", "Checked", "key-decide-1");
    expect(fetch.mock.calls.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"))).toEqual([
      "key-propose-1",
      "key-propose-2",
      "key-propose-3",
      "key-decide-1",
    ]);
    expect(fetch.mock.calls.map(([url, init]) => [String(url), init?.body ? JSON.parse(String(init.body)) : null])).toEqual([
      [`https://api.test/staff/clients/${CLIENT}/actions`, { kind: "pin_reset", reason: "Forgot PIN" }],
      [`https://api.test/staff/clients/${CLIENT}/actions`, { kind: "statement", reason: "Loan", statement: { date_from: "2026-09-01", date_to: "2026-10-31" } }],
      [
        `https://api.test/staff/clients/${CLIENT}/actions`,
        { kind: "withdrawal", reason: "Asked", withdrawal: { investment_id: "-Ni", destination_id: "-Nd", message_id: "-Nm" } },
      ],
      [`https://api.test/staff/clients/actions/${ACTION.id}/decision`, { decision: "approved", reason: "Checked" }],
    ]);
  });

  it.each([
    [422, "validation_failed", [{ field_path: ["body", "limits", "bank_per_day"], code: "not_below_shared" }], "not_below_shared"],
    [422, "validation_failed", [{ field_path: ["body", "statement", "date_to"], code: "period_in_future" }], "period"],
    [422, "validation_failed", [{ field_path: ["body", "withdrawal", "message_id"], code: "too_old" }], "too_old"],
    [422, "validation_failed", [{ field_path: ["body", "destination_id"], code: "destination_in_safety_wait" }], "account"],
    [409, "conflict", [], "waiting"],
    [409, "conflict", [{ field_path: ["path", "client_id"], code: "not_active" }], "not_active"],
    [409, "conflict", [{ field_path: ["path", "client_id"], code: "closed" }], "closed"],
    [404, "not_found", [], "no_client"],
    [403, "forbidden", [], "forbidden"],
  ])("turns a %i %s on a proposal into %s", async (status, code, fields, expected) => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => refusal(status, code, fields));
    await expect(apiWith(fetch).propose(CLIENT, { kind: "pin_reset", reason: "x" }, "key-refused")).rejects.toMatchObject({ code: expected });
  });

  it.each([
    [409, "conflict", "cannot_now"],
    [404, "not_found", "not_found"],
    [403, "confirm_it_is_you", "not_confirmed"],
  ])("turns a %i %s on a decision into %s", async (status, code, expected) => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => refusal(status, code));
    await expect(apiWith(fetch).decide(ACTION.id, "approved", "x", "key-refused")).rejects.toMatchObject({ code: expected });
  });
});
