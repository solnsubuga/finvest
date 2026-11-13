import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { ClientsScreen } from "./index.tsx";
import { createStubClientsApi } from "./stub.ts";
import { createHttpSuppressionsApi, notConnectedSuppressionsApi, SuppressionsError, type SuppressionsApi } from "./suppressions.ts";
import { createStubSuppressionsApi } from "./suppressionsStub.ts";

const ME = { id: "stub-admin", name: "Solomon N." };
const NOW = Date.parse("2026-11-13T09:00:00Z");

function renderScreen({
  address = "/clients",
  suppressions = createStubSuppressionsApi(ME, () => NOW),
  canLift = true,
}: { address?: string; suppressions?: SuppressionsApi; canLift?: boolean } = {}) {
  render(
    <MemoryRouter initialEntries={[address]}>
      <ClientsScreen api={createStubClientsApi(ME, () => NOW)} suppressions={suppressions} canLift={canLift} />
    </MemoryRouter>,
  );
  return userEvent.setup();
}

async function lookUp(user: ReturnType<typeof userEvent.setup>, email: string) {
  await user.clear(screen.getByLabelText("Look up an email address"));
  await user.type(screen.getByLabelText("Look up an email address"), email);
  await user.click(screen.getByRole("button", { name: "Look up" }));
}

describe("email suppressions", () => {
  it("lists suppressed addresses and approves someone else's lift with a reason", async () => {
    const user = renderScreen();
    const list = await screen.findByRole("list", { name: "Suppressed addresses" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByText(/1 with a lift waiting for a second person/)).toBeInTheDocument();

    await lookUp(user, "Sarah.N@example.com");
    const card = await screen.findByRole("article", { name: "Sarah.N@example.com" });
    expect(within(card).getByText(/Proposed by Grace Auma/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Approve" }));
    await user.type(within(card).getByLabelText(/^Why approve/), "Sent a test email and it arrived");
    await user.click(within(card).getByRole("button", { name: "Approve the lift" }));
    expect(await screen.findByText(/Finvest emails this address again, until the next lasting bounce/)).toBeInTheDocument();
    expect(within(card).getByText("Lifted")).toBeInTheDocument();
  });

  it("never offers to lift an address that sent a spam complaint", async () => {
    const user = renderScreen();
    await screen.findByRole("list", { name: "Suppressed addresses" });
    await lookUp(user, "j.okot@example.com");
    const card = await screen.findByRole("article", { name: "j.okot@example.com" });
    expect(within(card).getByText(/Finvest never emails this address again/)).toBeInTheDocument();
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
  });

  it("proposes a lift that only someone else can approve", async () => {
    const user = renderScreen();
    await screen.findByRole("list", { name: "Suppressed addresses" });
    await lookUp(user, "old.address@example.com");
    const card = await screen.findByRole("article", { name: "old.address@example.com" });
    await user.click(within(card).getByRole("button", { name: "Propose a lift" }));
    await user.type(within(card).getByLabelText(/^Why email this address again/), "Client confirmed the address on a call");
    await user.click(within(card).getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByText(/Someone else must approve the lift/)).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
  });

  it("says when an address was never suppressed, and shows a role that cannot lift no buttons", async () => {
    const user = renderScreen({ canLift: false });
    await screen.findByRole("list", { name: "Suppressed addresses" });
    await lookUp(user, "new.person@example.com");
    expect(await screen.findByText("Finvest has never stopped emailing new.person@example.com.")).toBeInTheDocument();
    await lookUp(user, "sarah.n@example.com");
    const card = await screen.findByRole("article", { name: "sarah.n@example.com" });
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card).getByText(/Lifting needs support, operations or an admin/)).toBeInTheDocument();
  });

  it("shows on a client's page that Finvest has stopped emailing them", async () => {
    renderScreen({ address: "/clients?client=-NstubClient00000042" });
    const card = await screen.findByRole("article", { name: "Email to sarah.n@example.com" });
    expect(within(card).getByText("Lift waiting")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Email suppressions" })).not.toBeInTheDocument();
  });

  it("says when the list cannot be loaded", async () => {
    renderScreen({ suppressions: notConnectedSuppressionsApi });
    expect(await screen.findByRole("heading", { name: "Email suppressions could not be loaded" })).toBeInTheDocument();
  });
});

describe("email suppressions over HTTP", () => {
  it("sends the address in the body of a POST, never in the URL", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      Promise.resolve(new Response(JSON.stringify({ suppression: null }), { status: 200, headers: { "Content-Type": "application/json" } })),
    );
    const api = createHttpSuppressionsApi(createHttp("https://api.test", fetch));
    expect(await api.lookup(" sarah@example.com ")).toBeNull();
    const [url, init] = fetch.mock.calls[0] ?? [];
    expect(url).toBe("https://api.test/staff/email-suppressions/lookup");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ email: "sarah@example.com" });
  });

  it("turns the API's refusals into the screen's errors", async () => {
    const answers = [
      { status: 409, body: { error: { code: "conflict", fields: [{ field_path: ["body", "address_hash"], code: "complained" }] } } },
      { status: 409, body: { error: { code: "conflict", fields: [] } } },
      { status: 422, body: { error: { code: "validation_failed", fields: [{ field_path: ["body", "email"], code: "invalid" }] } } },
      { status: 403, body: { error: { code: "forbidden", fields: [] } } },
    ];
    const fetch = vi.fn<typeof globalThis.fetch>(() => {
      const next = answers.shift();
      return next
        ? Promise.resolve(new Response(JSON.stringify(next.body), { status: next.status, headers: { "Content-Type": "application/json" } }))
        : Promise.reject(new TypeError("offline"));
    });
    const api = createHttpSuppressionsApi(createHttp("https://api.test", fetch));
    const hash = "a".repeat(64);
    await expect(api.propose(hash, "x")).rejects.toEqual(new SuppressionsError("complained"));
    await expect(api.propose(hash, "x")).rejects.toMatchObject({ code: "waiting" });
    await expect(api.lookup("nope")).rejects.toMatchObject({ code: "invalid_email" });
    await expect(api.decide("-Nlift00000000000000", "approved", "x")).rejects.toMatchObject({ code: "same_person" });
    await expect(api.list()).rejects.toMatchObject({ code: "unavailable" });
  });
});
