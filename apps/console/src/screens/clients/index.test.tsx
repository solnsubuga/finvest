import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { notConnectedClientsApi, type ClientsApi } from "./api.ts";
import { ClientsScreen } from "./index.tsx";
import { createStubClientsApi } from "./stub.ts";
import type { SuppressionsApi } from "./suppressions.ts";
import { createStubSuppressionsApi } from "./suppressionsStub.ts";

const ME = { id: "stub-admin", name: "Solomon N." };
const NOW = Date.parse("2026-11-13T09:00:00Z");

function renderClients(
  api: ClientsApi = createStubClientsApi(ME, () => NOW),
  address = "/clients",
  suppressions: SuppressionsApi = createStubSuppressionsApi(ME, () => NOW),
  canLift = true,
) {
  render(
    <MemoryRouter initialEntries={[address]}>
      <ClientsScreen api={api} suppressions={suppressions} canLift={canLift} />
    </MemoryRouter>,
  );
  return { api, user: userEvent.setup() };
}

async function find(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByLabelText("Find a client"), text);
  await user.click(screen.getByRole("button", { name: "Find" }));
}

describe("clients", () => {
  it("finds a client by account number and shows their page", async () => {
    const { user } = renderClients();
    expect(screen.getByText(/Find a client to see their account/)).toBeInTheDocument();
    await find(user, "1004 382 917");
    expect(await screen.findByRole("heading", { name: "Sarah Nakato" })).toBeInTheDocument();
    const holds = screen.getByRole("region", { name: "What Finvest holds for them" });
    expect(within(holds).getByText("UGX 2,500,000")).toBeInTheDocument();
    expect(within(holds).getByText("UGX 3,061,438")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Payout accounts" })).getByText(/MTN Mobile Money/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Help and complaints" })).getByText(/FC-2026-0118/)).toBeInTheDocument();
  });

  it("finds a client by a mobile number written the local way, and says when nobody matches", async () => {
    const { user } = renderClients();
    await find(user, "0701 987654");
    expect(await screen.findByRole("heading", { name: "James Okot" })).toBeInTheDocument();
    expect(screen.getByText(/This account is locked/)).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Find a client"));
    await find(user, "0700000000");
    expect(await screen.findByText(/No client has the account number, mobile number or ID/)).toBeInTheDocument();
  });

  it("lists every client newest first, a page at a time, and filters by status", async () => {
    const { user } = renderClients();
    const list = (await screen.findAllByRole("region", { name: "All clients" }))[0]!;
    await within(list).findAllByRole("rowheader");
    const names = () => within(list).getAllByRole("rowheader").map((cell) => cell.textContent);
    expect(names()).toEqual(["Name not verified yet", "James Okot", expect.any(String), expect.any(String)]);
    await user.click(screen.getByRole("button", { name: "Show more" }));
    await screen.findByText("Sarah Nakato");
    expect(names()).toHaveLength(6);
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Locked" }));
    expect(screen.getByRole("button", { name: "Locked" })).toHaveAttribute("aria-pressed", "true");
    await screen.findByText("James Okot");
    const locked = screen.getAllByRole("region", { name: "All clients" })[0]!;
    expect(within(locked).getAllByRole("rowheader")).toHaveLength(2);
  });

  it("finds clients by part of their name in any order, or their email, and opens one", async () => {
    const { user } = renderClients();
    await find(user, "nakato sar");
    const list = (await screen.findAllByRole("region", { name: "Clients matching “nakato sar”" }))[0]!;
    expect(await within(list).findAllByRole("rowheader")).toHaveLength(1);
    expect(within(list).getByText("sarah.n@example.com")).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "Sarah Nakato" }));
    expect(await screen.findByRole("heading", { name: "Sarah Nakato" })).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Find a client"));
    await find(user, "okot@");
    expect(await screen.findByRole("button", { name: "James Okot" })).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Find a client"));
    await find(user, "nobody here");
    expect(await screen.findByText("No client's verified name or email matches “nobody here”.")).toBeInTheDocument();
  });

  it("says when the list cannot be loaded", async () => {
    renderClients(notConnectedClientsApi);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Clients could not be loaded");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("approves someone else's lock with a reason, and the account locks", async () => {
    const { user } = renderClients(undefined, "/clients?client=-NstubClient00000042");
    const card = await screen.findByRole("article", { name: "Lock the account, waiting" });
    expect(within(card).getByText(/Proposed by Grace Auma/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Approve" }));
    await user.click(within(card).getByRole("button", { name: "Approve and lock" }));
    expect(within(card).getByText("Write a short reason for the audit trail.")).toBeInTheDocument();
    await user.type(within(card).getByLabelText(/^Why approve/), "Confirmed with the client on a call");
    await user.click(within(card).getByRole("button", { name: "Approve and lock" }));
    expect(await screen.findByText(/The account is locked and the client has been signed out everywhere/)).toBeInTheDocument();
    expect(screen.getByText(/This account is locked\. The client cannot sign in/)).toBeInTheDocument();
    const history = screen.getByRole("list", { name: "Earlier locks and unlocks" });
    expect(within(history).getByText(/Approved by Solomon N\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Propose an unlock" })).toBeInTheDocument();
  });

  it("proposes an unlock that only someone else can approve", async () => {
    const { user } = renderClients(undefined, "/clients?client=-NstubClient00000017");
    await user.click(await screen.findByRole("button", { name: "Propose an unlock" }));
    await user.type(screen.getByLabelText(/^Why\?/), "Client confirmed on a call it was them travelling");
    await user.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByText(/Someone else must approve the unlock/)).toBeInTheDocument();
    const card = screen.getByRole("article", { name: "Unlock the account, waiting" });
    expect(within(card).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
  });

  it("shows that the client froze their own account, and offers the two-person unlock", async () => {
    renderClients(undefined, "/clients?client=-NstubClient00000058");
    const card = await screen.findByRole("article", { name: "Frozen by the client" });
    expect(within(card).getByText("“Lost my phone on a boda this morning”")).toBeInTheDocument();
    expect(within(card).getByText(/check it is really them/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Propose an unlock" })).toBeInTheDocument();
  });

  it("does not call a lock by staff a freeze", async () => {
    renderClients(undefined, "/clients?client=-NstubClient00000017");
    expect(await screen.findByRole("button", { name: "Propose an unlock" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Frozen by the client" })).not.toBeInTheDocument();
  });

  it("offers no lock for an account that has not signed up yet", async () => {
    renderClients(undefined, "/clients?client=-NstubClient00000099");
    expect(await screen.findByRole("heading", { name: "Name not verified yet" })).toBeInTheDocument();
    expect(screen.getByText(/has not set up a sign-in yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Propose/ })).not.toBeInTheDocument();
  });

  it("shows a role that cannot lock what is waiting, without the buttons", async () => {
    renderClients(createStubClientsApi(ME, () => NOW, { canLock: false, seesSupport: false, seesComplaints: false }), "/clients?client=-NstubClient00000042");
    const card = await screen.findByRole("article", { name: "Lock the account, waiting" });
    expect(within(card).getByText("Your role cannot decide locks.")).toBeInTheDocument();
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Help and complaints" })).not.toBeInTheDocument();
  });

  it("says when a client cannot be loaded", async () => {
    renderClients(notConnectedClientsApi, "/clients?client=-NstubClient00000042");
    expect(await screen.findByRole("heading", { name: "The client could not be loaded" })).toBeInTheDocument();
    renderClients(undefined, "/clients?client=-NstubNobody00000000");
    expect(await screen.findByText("There is no client with that ID.")).toBeInTheDocument();
  });
});
