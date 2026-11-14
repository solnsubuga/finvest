import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { ReviewQueueScreen } from "./index.tsx";
import {
  createHttpOrganisationsApi,
  createStubOrganisationsApi,
  OrganisationsError,
  type OrganisationsApi,
} from "./organisations.ts";
import { createStubReviewQueueApi } from "./stub.ts";

const ME = { id: "sample-staff-me", name: "Ruth Nambi" };
const NOW = Date.parse("2026-11-14T09:00:00Z");

function renderScreen({
  organisations = createStubOrganisationsApi(ME.id, () => NOW),
  entry = "/review-queue?tab=organisations",
  canReviewOrganisations = true,
}: { organisations?: OrganisationsApi; entry?: string; canReviewOrganisations?: boolean } = {}) {
  const element = (
    <ReviewQueueScreen
      api={createStubReviewQueueApi(ME.id, () => NOW)}
      directory={createStubDirectory(ME)}
      organisations={organisations}
      canReviewOrganisations={canReviewOrganisations}
      me={ME.id}
      canDecide
      now={() => new Date(NOW)}
    />
  );
  const router = createMemoryRouter([{ path: "/review-queue", element }], { initialEntries: [entry] });
  render(<RouterProvider router={router} />);
  return { router, user: userEvent.setup() };
}

const panel = (name: string) => screen.findByRole("article", { name: `Application for ${name}` });

describe("review queue: company and group accounts", () => {
  it("is a tab only for someone who reviews them", async () => {
    renderScreen({ canReviewOrganisations: false });
    await screen.findByRole("tab", { name: /Payout accounts/ });
    expect(screen.queryByRole("tab", { name: /Company accounts/ })).toBeNull();
    // Asked for by address anyway, it shows payout accounts instead.
    expect(screen.getByRole("tab", { name: /Payout accounts/ })).toHaveAttribute("aria-selected", "true");
  });

  it("lists what waits and shows each signatory by the name on their ID", async () => {
    renderScreen();
    const list = await screen.findByRole("list", { name: "Company and group accounts" });
    expect(within(list).getAllByRole("button").map((b) => b.textContent)).toEqual([
      expect.stringContaining("Kira Road Savings Group"),
      expect.stringContaining("Nakato Traders Ltd"),
    ]);
    expect(await screen.findByRole("tab", { name: "Company accounts (2)" })).toHaveAttribute("aria-selected", "true");
    const review = await panel("Kira Road Savings Group");
    expect(within(review).getByText("Amara Nakato")).toBeInTheDocument();
    expect(within(review).getByText("Treasurer")).toBeInTheDocument();
    expect(within(review).getByText("Any 2 of 3 must approve")).toBeInTheDocument();
    expect(within(review).getByRole("button", { name: "Mark as checked" })).toBeInTheDocument();
  });

  it("gets five-minute links to the documents", async () => {
    const organisations = createStubOrganisationsApi(ME.id, () => NOW);
    const links = vi.spyOn(organisations, "documentLinks");
    const { user } = renderScreen({ organisations });
    const review = await panel("Kira Road Savings Group");
    await user.click(within(review).getByRole("button", { name: "Get links to the documents" }));
    expect(await within(review).findByRole("link", { name: "Open photo" })).toBeInTheDocument();
    expect(within(review).getByText(/work for five minutes/)).toBeInTheDocument();
    expect(links).toHaveBeenCalledWith("-NstubOrg00000000001");
  });

  it("needs a note, and checking leaves approval to someone else", async () => {
    const { user } = renderScreen();
    const review = await panel("Kira Road Savings Group");
    await user.click(within(review).getByRole("button", { name: "Mark as checked" }));
    expect(within(review).getByText("Write a note for the audit trail.")).toBeInTheDocument();
    await user.type(within(review).getByLabelText("Note"), "Constitution and minutes name all three.");
    await user.click(within(review).getByRole("button", { name: "Mark as checked" }));
    expect(await screen.findByText(/A different member of staff now approves it/)).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Company and group accounts" });
    expect(await within(list).findByRole("button", { name: /Checked by you, needs a second person/ })).toHaveTextContent("Kira Road");
  });

  it("approves a colleague's check after asking once more, and shows the account number", async () => {
    const { user } = renderScreen({ entry: "/review-queue?tab=organisations&organisation=-NstubOrg00000000002" });
    const review = await panel("Nakato Traders Ltd");
    expect(within(review).getByText(/Ruth Namata/)).toBeInTheDocument();
    await user.type(within(review).getByLabelText("Note"), "URSB record matches.");
    await user.click(within(review).getByRole("button", { name: "Approve and open the account" }));
    const asked = within(review).getByRole("alertdialog", { name: "Confirm" });
    await user.click(within(asked).getByRole("button", { name: "Yes, approve" }));
    expect(await screen.findByText(/has its own account number, 30/)).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Company accounts (1)" })).toBeInTheDocument();
  });

  it("sends it back with what to change", async () => {
    const organisations = createStubOrganisationsApi(ME.id, () => NOW);
    const decide = vi.spyOn(organisations, "decide");
    const { user } = renderScreen({ organisations });
    const review = await panel("Kira Road Savings Group");
    await user.click(within(review).getByRole("button", { name: "Send back" }));
    expect(within(review).getByText("Say what the applicant must change.")).toBeInTheDocument();
    await user.type(within(review).getByLabelText("Note"), "The resolution's second page is missing.");
    await user.click(within(review).getByRole("button", { name: "Send back" }));
    expect(await screen.findByText(/The applicant sees your note/)).toBeInTheDocument();
    expect(decide).toHaveBeenCalledWith("-NstubOrg00000000001", "send_back", "The resolution's second page is missing.");
  });

  it("says so when nothing waits, and when it cannot be loaded", async () => {
    renderScreen({ organisations: createStubOrganisationsApi(ME.id, () => NOW, "empty") });
    expect(await screen.findByText("No applications waiting")).toBeInTheDocument();
  });

  it("offers to try again when the list cannot be loaded", async () => {
    renderScreen({ organisations: createStubOrganisationsApi(ME.id, () => NOW, "error") });
    expect(await screen.findByText("Company and group accounts could not be loaded")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("company and group accounts over the API", () => {
  function http(answer: (method: string, path: string, body: unknown) => unknown): Http & { seen: [string, string, unknown][] } {
    const seen: [string, string, unknown][] = [];
    return {
      seen,
      async request<T>(method: string, path: string, body?: unknown) {
        seen.push([method, path, body]);
        return answer(method, path, body) as T;
      },
      download: () => Promise.reject(new Error("not used")),
    };
  }

  const json = {
    id: "O1",
    kind: "company",
    name: "Nakato Traders Ltd",
    registration_number: "80020001234567",
    approvals_needed: 1,
    status: "checked",
    applicant_id: "C1",
    created_at: "2026-11-11T08:00:00Z",
    submitted_at: "2026-11-11T09:00:00Z",
    return_note: null,
    returned_at: null,
    decided_at: null,
    account_number: null,
    documents_needed: ["certificate_of_incorporation", "board_resolution"],
    documents: [],
    problems: [],
    signatories: [
      {
        client_id: "C1",
        role: "Director",
        name: "Brian K.",
        verified_name: "Brian Kato",
        account_number: "1001004903",
        is_applicant: true,
        status: "joined",
        invited_at: "2026-11-11T08:00:00Z",
        answered_at: "2026-11-11T08:00:00Z",
      },
    ],
    checked_by: "S1",
    checked_by_name: "Grace Auma",
    checked_at: "2026-11-12T08:00:00Z",
    check_note: "Fine.",
    decided_by: null,
    decided_by_name: null,
    decision_note: null,
    can_check: false,
    can_approve: true,
    can_send_back: true,
    can_reject: true,
  };

  it("posts each decision to its own route, with the note", async () => {
    const api = http(() => json);
    const organisations = createHttpOrganisationsApi(api);
    const found = await organisations.decide("O1", "approve", "Matches.");
    await organisations.decide("O1", "send_back", "Page missing.");
    await organisations.decide("O1", "reject", "Not a company.");
    await organisations.decide("O1", "check", "Checked.");
    expect(api.seen.map(([method, path]) => `${method} ${path}`)).toEqual([
      "POST /staff/organisations/O1/approval",
      "POST /staff/organisations/O1/return",
      "POST /staff/organisations/O1/rejection",
      "POST /staff/organisations/O1/check",
    ]);
    expect(api.seen[0]?.[2]).toEqual({ note: "Matches." });
    expect(found).toMatchObject({
      check: { byName: "Grace Auma", note: "Fine." },
      signatories: [{ shownName: "Brian K.", verifiedName: "Brian Kato" }],
      canApprove: true,
    });
  });

  it("names a signatory no longer approved apart from a move someone else made", async () => {
    const refuse = (code: string) =>
      createHttpOrganisationsApi(
        http(() => {
          throw new ApiError("conflict", 409, [{ field_path: ["x"], code }]);
        }),
      );
    await expect(refuse("signatory_not_verified").decide("O1", "check", "x")).rejects.toMatchObject({ code: "signatory_not_verified" });
    await expect(refuse("already_approved").decide("O1", "check", "x")).rejects.toBeInstanceOf(OrganisationsError);
    await expect(refuse("already_approved").decide("O1", "check", "x")).rejects.toMatchObject({ code: "already_decided" });
  });
});
