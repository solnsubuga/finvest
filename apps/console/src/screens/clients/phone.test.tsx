import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import type { Directory } from "@/directory/api.ts";
import { PhoneSection } from "./Phone.tsx";
import { createHttpPhoneChangesApi, createStubPhoneChangesApi, normalisePhone, type PhoneChangesApi } from "./phone.ts";

const ME = { id: "stub-operations" };
const NOW = Date.parse("2026-11-14T09:00:00Z");
const WITH_CHANGE = "-NstubClient00000042";
const WITHOUT = "-NstubClient00000017";

function renderPhone(clientId: string, api: PhoneChangesApi = createStubPhoneChangesApi(ME, () => NOW, 0)) {
  const onChanged = vi.fn();
  render(<PhoneSection api={api} clientId={clientId} currentPhone="+256772123456" onChanged={onChanged} />);
  return { user: userEvent.setup(), onChanged };
}

describe("changing a client's mobile number", () => {
  it("approves a colleague's proposal, and the client page reloads", async () => {
    const { user, onChanged } = renderPhone(WITH_CHANGE);
    const card = await screen.findByRole("article", { name: "New mobile number, waiting" });
    expect(within(card).getByRole("heading")).toHaveTextContent("+256 772 123 456 to +256 701 234 567");
    expect(within(card).getByText(/Proposed by Grace Auma/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Approve new number" }));
    expect(await screen.findByRole("status")).toHaveTextContent("The number is now +256 701 234 567");
    expect(onChanged).toHaveBeenCalledOnce();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("rejects one with a reason", async () => {
    const { user, onChanged } = renderPhone(WITH_CHANGE);
    const card = await screen.findByRole("article", { name: "New mobile number, waiting" });
    await user.click(within(card).getByRole("button", { name: "Reject" }));
    await user.click(within(card).getByRole("button", { name: "Reject" }));
    expect(within(card).getByText("Write a short reason for the audit trail.")).toBeInTheDocument();
    await user.type(within(card).getByLabelText(/Why reject/), "Could not reach the client on the new number");
    await user.click(within(card).getByRole("button", { name: "Reject" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Rejected. The number did not change.");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("proposes a new number, which then waits for someone else", async () => {
    const { user } = renderPhone(WITHOUT);
    await user.click(await screen.findByRole("button", { name: "Change mobile number" }));
    const form = screen.getByRole("form", { name: "Propose a new mobile number" });
    await user.type(within(form).getByLabelText(/New number/), "0712 34 56");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getAllByText(/Write a Ugandan mobile number/).length).toBeGreaterThan(0);
    expect(within(form).getByText("Write a short reason for the audit trail.")).toBeInTheDocument();
    await user.clear(within(form).getByLabelText(/New number/));
    await user.type(within(form).getByLabelText(/New number/), "0712 345 678");
    await user.type(within(form).getByLabelText(/Why/), "Called back on the old number, client confirmed");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const card = await screen.findByRole("article", { name: "New mobile number, waiting" });
    expect(within(card).getByText(/Proposed by you/)).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Approve new number" })).not.toBeInTheDocument();
    expect(within(card).getByText(/someone else approves or rejects it/)).toBeInTheDocument();
  });

  it("says when the number already has an account", async () => {
    const { user } = renderPhone(WITHOUT);
    await user.click(await screen.findByRole("button", { name: "Change mobile number" }));
    const form = screen.getByRole("form", { name: "Propose a new mobile number" });
    await user.type(within(form).getByLabelText(/New number/), "0772 000 111");
    await user.type(within(form).getByLabelText(/Why/), "Client asked");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("already belongs to another Finvest account");
  });

  it("reads Ugandan numbers the way the backend does", () => {
    expect(normalisePhone("0772 123 456")).toBe("+256772123456");
    expect(normalisePhone("+256 772-123-456")).toBe("+256772123456");
    expect(normalisePhone("256772123456")).toBe("+256772123456");
    expect(normalisePhone("0412 123 456")).toBeNull();
  });
});

const CHANGE = {
  id: "-NphoneChange000001",
  client_id: "-Nclient00000000042",
  new_phone: "+256701234567",
  reason: "Stolen SIM",
  proposed_by: "-Nstaff0000000GRACE",
  proposed_at: "2026-11-14T07:00:00Z",
  expires_at: "2026-11-15T07:00:00Z",
};

const directory: Directory = {
  client: () => Promise.resolve(null),
  findClients: () => Promise.resolve([]),
  staffName: (id) => Promise.resolve(id === CHANGE.proposed_by ? "Grace Auma" : null),
  team: () => Promise.resolve([]),
};

function reply(status: number, body?: unknown) {
  return Promise.resolve(
    body === undefined ? new Response(null, { status }) : new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
}

function refusal(status: number, code: string, fields: unknown[] = []) {
  return reply(status, { request_id: "r", error: { code, message: code, fields } });
}

describe("number changes over HTTP", () => {
  const apiWith = (fetch: typeof globalThis.fetch) =>
    createHttpPhoneChangesApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)), directory, "-Nstaff00000000ME01");

  it("finds the change waiting for one client and names who proposed it", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, { items: [{ ...CHANGE, client_id: "-Nother" }, CHANGE] }));
    const change = await apiWith(fetch).waitingFor(CHANGE.client_id);
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/phone-changes");
    expect(change).toEqual({
      id: CHANGE.id,
      clientId: CHANGE.client_id,
      newPhone: "+256701234567",
      reason: "Stolen SIM",
      proposedBy: CHANGE.proposed_by,
      proposedByName: "Grace Auma",
      proposedAt: CHANGE.proposed_at,
      expiresAt: CHANGE.expires_at,
      yours: false,
    });
    expect(await apiWith(vi.fn<typeof globalThis.fetch>(() => reply(200, { items: [] }))).waitingFor("x")).toBeNull();
  });

  it("proposes, approves and rejects on the backend's paths", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>((url) => (String(url).endsWith("/phone-changes") ? reply(201, { id: "x" }) : reply(204)));
    const api = apiWith(fetch);
    await api.propose("-Nclient00000000042", "0701 234 567", "Stolen SIM");
    await api.approve(CHANGE.id);
    await api.reject(CHANGE.id, "No answer");
    expect(fetch.mock.calls.map(([url, init]) => [String(url), init?.method, init?.body ? JSON.parse(String(init.body)) : null])).toEqual([
      ["https://api.test/staff/clients/-Nclient00000000042/phone-changes", "POST", { phone: "0701 234 567", reason: "Stolen SIM" }],
      [`https://api.test/staff/phone-changes/${CHANGE.id}/approval`, "POST", null],
      [`https://api.test/staff/phone-changes/${CHANGE.id}/rejection`, "POST", { note: "No answer" }],
    ]);
  });

  it.each([
    [422, "validation_failed", [{ field_path: ["body", "phone"], code: "invalid" }], "invalid_phone"],
    [409, "phone_taken", [], "phone_taken"],
    [409, "conflict", [], "waiting"],
    [410, "expired", [], "expired"],
    [403, "forbidden", [], "forbidden"],
    [403, "confirm_it_is_you", [], "not_confirmed"],
  ])("turns a %i %s into %s", async (status, code, fields, expected) => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => refusal(status, code, fields));
    await expect(apiWith(fetch).approve(CHANGE.id)).rejects.toMatchObject({ code: expected });
  });
});
