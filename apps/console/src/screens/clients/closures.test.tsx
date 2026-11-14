import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createHttpClosuresApi, createStubClosuresApi, ClosuresError } from "./closures.ts";
import { standingLines } from "./Closures.tsx";
import { ClientsScreen } from "./index.tsx";
import { createStubProfileApi } from "./profile.ts";
import { createStubClientsApi } from "./stub.ts";
import { createStubSuppressionsApi } from "./suppressionsStub.ts";

const ME = { id: "stub-admin", name: "Solomon N." };
const NOW = Date.parse("2026-11-14T09:00:00Z");

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function renderScreen(address: string) {
  const clients = createStubClientsApi(ME, () => NOW);
  const closures = createStubClosuresApi(ME, () => NOW, (id) => clients.closeAccount(id));
  render(
    <MemoryRouter initialEntries={[address]}>
      <ClientsScreen
        api={clients}
        suppressions={createStubSuppressionsApi(ME, () => NOW)}
        canLift
        closures={closures}
        profiles={createStubProfileApi(() => NOW)}
      />
    </MemoryRouter>,
  );
  return userEvent.setup();
}

const STANDING = { running_investments: 0, owed: [], open_deposits: 0, open_payouts: 0, open_goals: 0, live_plans: 0, blockers: [] };
const CLOSURE = {
  id: "-Nclosure00000000001",
  client_id: "-Nclient00000000000A",
  status: "prepared",
  reason: null,
  requested_at: "2026-11-13T07:00:00Z",
  decision_note: null,
  decided_at: null,
  cancelled_at: null,
  verified_name: "Sarah Nakato",
  account_number: "1004382917",
  prepared_by: "-Nstaff000000000000A",
  prepared_by_name: "Grace Auma",
  prepared_at: "2026-11-13T08:00:00Z",
  prepared_note: "Checked",
  decided_by: null,
  decided_by_name: null,
  standing: STANDING,
  can_prepare: false,
  can_approve: true,
  can_decline: true,
};

describe("account closures", () => {
  it("lists open requests on the landing page and opens the client", async () => {
    const user = renderScreen("/clients");
    const queue = await screen.findByRole("list", { name: "Requests to close an account" });
    expect(within(queue).getByText("Peter Mukasa")).toBeInTheDocument();
    expect(within(queue).getByText(/Checked once, waiting for a second person/)).toBeInTheDocument();
    await user.click(within(queue).getAllByRole("button", { name: "Open" })[0]!);
    expect(await screen.findByRole("heading", { name: "Peter Mukasa" })).toBeInTheDocument();
  });

  it("approves someone else's prepared request with a note, and the account closes", async () => {
    const user = renderScreen("/clients?client=-NstubClient00000077");
    const card = await screen.findByRole("article", { name: "Request to close, open" });
    expect(within(card).getByText(/Prepared by Grace Auma/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Approve and close" }));
    await user.type(within(card).getByLabelText(/Why approve/), "Second check done");
    await user.click(within(card).getByRole("button", { name: "Approve and close" }));
    expect(await screen.findByText(/The account is closed and the client has been signed out everywhere/)).toBeInTheDocument();
    expect(await screen.findByText("Closed")).toBeInTheDocument();
  });

  it("prepares a request, then leaves the approval to someone else", async () => {
    const user = renderScreen("/clients?client=-NstubClient00000063");
    const card = await screen.findByRole("article", { name: "Request to close, open" });
    expect(within(card).getByText("The client gave no reason.")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Prepare for closing" }));
    await user.type(within(card).getByLabelText(/What did you check/), "Nothing running");
    await user.click(within(card).getByRole("button", { name: "Prepare for closing" }));
    expect(await screen.findByText(/A different person must approve it/)).toBeInTheDocument();
    expect(await screen.findByText(/You prepared this, so someone else approves it/)).toBeInTheDocument();
    const after = screen.getByRole("article", { name: "Request to close, open" });
    expect(within(after).queryByRole("button", { name: "Approve and close" })).toBeNull();
  });

  it("shows next of kin with masked ID numbers and the investing answers", async () => {
    renderScreen("/clients?client=-NstubClient00000042");
    const kin = await screen.findByRole("region", { name: "Next of kin" });
    expect(within(kin).getByText("Joseph Nakato")).toBeInTheDocument();
    expect(within(kin).queryByText(/%/)).toBeNull();
    expect(within(kin).getByText(/CM••••••••12AB/)).toBeInTheDocument();
    const risk = screen.getByRole("region", { name: "Investing questions" });
    expect(within(risk).getByText("Balanced")).toBeInTheDocument();
    expect(within(risk).getByText("In 1 to 3 years")).toBeInTheDocument();
  });

  it("names what is still open in whole amounts", () => {
    expect(
      standingLines({ runningInvestments: 2, owed: [{ minor: 8_164_760, currency: "UGX" }], openDeposits: 0, openPayouts: 1, openGoals: 0, livePlans: 0, blockers: [] }),
    ).toEqual(["2 investments running", "UGX 8,164,760 still owed to the client", "1 payout on its way"]);
  });
});

describe("account closures over HTTP", () => {
  it("reads the queue and sends each step's note to its own route", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((input) => (String(input).endsWith("/staff/closures") ? reply(200, { items: [CLOSURE] }) : reply(200, { ...CLOSURE, status: "closed" })));
    const api = createHttpClosuresApi(createHttp("https://api.test", fetch));
    const [item] = await api.open();
    expect(item).toMatchObject({ clientId: CLOSURE.client_id, preparedByName: "Grace Auma", canApprove: true });
    await api.approve(CLOSURE.id, "Second check");
    const call = fetch.mock.calls[1];
    expect(call?.[0]).toBe(`https://api.test/staff/closures/${CLOSURE.id}/approval`);
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ note: "Second check" });
  });

  it("tells a refusal for something still open apart from other conflicts", async () => {
    const answers = [
      reply(409, { error: { code: "conflict", fields: [{ field_path: ["blockers"], code: "money_owed" }] } }),
      reply(409, { error: { code: "conflict", fields: [] } }),
      reply(403, { error: { code: "forbidden", fields: [] } }),
      reply(429, { error: { code: "rate_limited", fields: [] } }),
    ];
    const fetch = vi.fn<typeof globalThis.fetch>(() => answers.shift() ?? Promise.reject(new TypeError("offline")));
    const api = createHttpClosuresApi(createHttp("https://api.test", fetch));
    await expect(api.prepare(CLOSURE.id, "x")).rejects.toEqual(new ClosuresError("blocked"));
    await expect(api.prepare(CLOSURE.id, "x")).rejects.toMatchObject({ code: "moved" });
    await expect(api.approve(CLOSURE.id, "x")).rejects.toMatchObject({ code: "same_person" });
    await expect(api.decline(CLOSURE.id, "x")).rejects.toMatchObject({ code: "refused" });
    await expect(api.open()).rejects.toMatchObject({ code: "unavailable" });
  });
});
