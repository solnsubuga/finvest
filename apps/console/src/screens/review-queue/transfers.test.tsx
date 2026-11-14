import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { notConnectedReviewQueueApi } from "./api.ts";
import { ReviewQueueScreen } from "./index.tsx";
import { createHttpTransfersApi, createStubTransfersApi, transfersErrorMessage, type TransfersApi } from "./transfers.ts";

const ME = { id: "sample-staff-me", name: "Ruth Nambi" };
const NOW = Date.parse("2026-11-14T09:00:00Z");

function renderScreen({
  transfers = createStubTransfersApi(ME.id, () => NOW),
  entry = "/review-queue?tab=transfers",
  canDecide = true,
  newKey = () => crypto.randomUUID(),
}: { transfers?: TransfersApi; entry?: string; canDecide?: boolean; newKey?: () => string } = {}) {
  const element = (
    <ReviewQueueScreen
      api={notConnectedReviewQueueApi}
      transfers={transfers}
      directory={createStubDirectory(ME)}
      me={ME.id}
      canDecide={false}
      canDecideTransfers={canDecide}
      now={() => new Date(NOW)}
      newKey={newKey}
    />
  );
  const router = createMemoryRouter([{ path: "/review-queue", element }], { initialEntries: [entry] });
  render(<RouterProvider router={router} />);
  return { user: userEvent.setup() };
}

describe("review queue: held transfers", () => {
  it("lists held transfers with both clients by their full names", async () => {
    renderScreen();
    const list = await screen.findByRole("list", { name: "Held transfers" });
    expect(await within(list).findByText(/Joan Namutebi to Sarah Achieng/)).toBeInTheDocument();
    expect(within(list).getByText(/Peter Okello to Amara Nakato/)).toBeInTheDocument();
    expect(within(list).getByText("Proposed by Daniel Kato, needs a second person")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Held transfers (2)" })).toBeInTheDocument();
    const review = await screen.findByRole("article", { name: "Held transfer from Joan Namutebi" });
    expect(within(review).getByText("Joan N. and Sarah A.")).toBeInTheDocument();
    expect(within(review).getByText("For the land in Mukono")).toBeInTheDocument();
  });

  it("proposes letting one through, then waits for someone else", async () => {
    const { user } = renderScreen();
    const review = await screen.findByRole("article", { name: "Held transfer from Joan Namutebi" });
    await user.type(within(review).getByLabelText("Reason for your decision"), "Called Joan, she knows Sarah");
    await user.click(within(review).getByRole("button", { name: "Propose release" }));
    const updated = await screen.findByText(/You proposed letting this transfer through/);
    expect(updated).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve release" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Withdraw proposal" })).toBeInTheDocument();
  });

  it("approves a colleague's proposal after asking once more", async () => {
    const { user } = renderScreen({ entry: "/review-queue?tab=transfers&transfer=sample-transfer-2" });
    const review = await screen.findByRole("article", { name: "Held transfer from Peter Okello" });
    expect(within(review).getByText(/Paying his sister/)).toBeInTheDocument();
    await user.type(within(review).getByLabelText("Reason for your decision"), "Agree");
    await user.click(within(review).getByRole("button", { name: "Approve release" }));
    await user.click(within(review).getByRole("button", { name: "Yes, approve" }));
    expect(await screen.findByText(/Let through. The worker moves it/)).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Held transfers (1)" })).toBeInTheDocument();
  });

  it("declines with a reason after asking once more", async () => {
    const { user } = renderScreen();
    const review = await screen.findByRole("article", { name: "Held transfer from Joan Namutebi" });
    await user.type(within(review).getByLabelText("Reason for your decision"), "Sender did not recognise it");
    await user.click(within(review).getByRole("button", { name: "Decline" }));
    await user.click(within(review).getByRole("button", { name: "Yes, decline" }));
    expect(await screen.findByText(/Declined. Nothing moved/)).toBeInTheDocument();
  });

  it("lets staff without the permission look but not decide", async () => {
    renderScreen({ canDecide: false });
    const review = await screen.findByRole("article", { name: "Held transfer from Joan Namutebi" });
    expect(within(review).getByText(/needs the investment transfers permission/)).toBeInTheDocument();
    expect(within(review).queryByRole("button", { name: "Decline" })).not.toBeInTheDocument();
  });

  it("shows nothing held", async () => {
    renderScreen({ transfers: createStubTransfersApi(ME.id, () => NOW, "empty") });
    expect(await screen.findByText("No transfers held")).toBeInTheDocument();
  });
});

describe("the sample held transfers", () => {
  it("refuses approving your own proposal and withdrawing someone else's, as the backend does", async () => {
    const api = createStubTransfersApi(ME.id, () => NOW);
    await api.proposeRelease("sample-transfer-1", "ok", "k1");
    await expect(api.approveRelease("sample-transfer-1", "ok", "k2")).rejects.toMatchObject({ code: "forbidden" });
    await expect(api.withdrawRelease("sample-transfer-2")).rejects.toMatchObject({ code: "forbidden" });
    await expect(api.proposeRelease("sample-transfer-1", "other", "k1")).rejects.toMatchObject({ code: "key_reused" });
  });
});

function reply(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const JSON_TRANSFER = {
  id: "-Ntransfer0000000001",
  investment_id: "-Ninvest000000000001",
  sender_client_id: "-Nclient00000000000A",
  recipient_client_id: "-Nclient00000000000B",
  sender_name: "Joan N.",
  recipient_name: "Sarah A.",
  amount_minor: 15000000,
  currency: "UGX",
  whole: false,
  message: null,
  status: "release_proposed",
  requested_at: "2026-11-14T06:00:00Z",
  release_proposed_by: "-Nstaff000000000000A",
  release_proposed_at: "2026-11-14T07:00:00Z",
  release_reason: "Called her",
};

describe("held transfers over HTTP", () => {
  it("reads the held list and sends each step with its key", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(200, [JSON_TRANSFER]));
    const api = createHttpTransfersApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));
    const [held] = await api.listHeld();
    expect(fetch.mock.calls[0]![0]).toBe("https://api.test/staff/investment-transfers/held");
    expect(held).toMatchObject({ amount: { minor: 15_000_000, currency: "UGX" }, release: { reason: "Called her" } });

    fetch.mockImplementation(() => reply(200, JSON_TRANSFER));
    await api.approveRelease(JSON_TRANSFER.id, "Agree", "key-1");
    const [url, init] = fetch.mock.calls[1]!;
    expect(url).toBe(`https://api.test/staff/investment-transfers/${JSON_TRANSFER.id}/approve`);
    expect(JSON.parse(String(init?.body))).toEqual({ reason: "Agree" });
    expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-1");
  });

  it("names the refusals", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(403, { request_id: "r", error: { code: "forbidden", message: "x", fields: [] } }),
    );
    const api = createHttpTransfersApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)));
    const error = await api.approveRelease("x", "y", "k").catch((caught: unknown) => caught);
    expect(transfersErrorMessage(error)).toMatch(/A different person must do this/);
  });
});
