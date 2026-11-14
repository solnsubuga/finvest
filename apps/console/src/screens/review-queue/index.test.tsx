import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { ReviewQueueError, type ReviewQueueApi } from "./api.ts";
import { ReviewQueueScreen } from "./index.tsx";
import { compareNames, createStubReviewQueueApi, type StubCase } from "./stub.ts";

const ME = { id: "sample-staff-me", name: "Ruth Nambi" };
const NOW = Date.parse("2026-11-14T09:00:00Z");

function renderScreen({
  api = createStubReviewQueueApi(ME.id, () => NOW),
  entry = "/review-queue",
  canDecide = true,
  newKey = () => crypto.randomUUID(),
}: { api?: ReviewQueueApi; entry?: string; canDecide?: boolean; newKey?: () => string } = {}) {
  const element = (
    <ReviewQueueScreen api={api} directory={createStubDirectory(ME)} me={ME.id} canDecide={canDecide} now={() => new Date(NOW)} newKey={newKey} />
  );
  const router = createMemoryRouter([{ path: "/review-queue", element }], { initialEntries: [entry] });
  render(<RouterProvider router={router} />);
  return { api, router, user: userEvent.setup() };
}

function stub(sample: StubCase = "sample") {
  return createStubReviewQueueApi(ME.id, () => NOW, sample);
}

async function openAccount(user: ReturnType<typeof userEvent.setup>, client: string) {
  const list = await screen.findByRole("list", { name: "Payout accounts" });
  const entry = await within(list).findByRole("button", { name: new RegExp(client) });
  await user.click(entry);
  return screen.findByRole("article", { name: `Payout account for ${client}` });
}

/** A decision redraws the account, so it is found again rather than held from before. */
function reopened(client: string) {
  return screen.findByRole("article", { name: `Payout account for ${client}` });
}

describe("review queue: payout accounts", () => {
  it("lists accounts oldest first and opens none until a person chooses one", async () => {
    const api = stub();
    const opened = vi.spyOn(api, "openAccount");
    renderScreen({ api });
    const list = await screen.findByRole("list", { name: "Payout accounts" });
    expect(await within(list).findByText("Sarah Achieng")).toBeInTheDocument();
    expect(within(list).getAllByRole("button").map((button) => button.textContent)).toEqual([
      expect.stringContaining("Sarah Achieng"),
      expect.stringContaining("Joan Namutebi"),
      expect.stringContaining("Peter Okello"),
      expect.stringContaining("Amara Nakato"),
      expect.stringContaining("David Mugisha"),
    ]);
    expect(within(list).getByRole("button", { name: /Peter Okello/ })).toHaveTextContent("Checked by Grace Auma, needs a second person");
    expect(within(list).getByRole("button", { name: /Joan Namutebi/ })).toHaveTextContent("Checked by you");
    expect(screen.getByText(/Choose an account to check it/)).toBeInTheDocument();
    expect(opened).not.toHaveBeenCalled();
    expect(screen.getByRole("tab", { name: "Payout accounts (5)" })).toHaveAttribute("aria-selected", "true");
  });

  it("shows the whole number, the ID name and the check once opened", async () => {
    const { user } = renderScreen();
    const review = await openAccount(user, "Peter Okello");
    expect(within(review).getByText("9030012744821")).toBeInTheDocument();
    expect(within(review).getByText("OKELLO PETER JAMES")).toBeInTheDocument();
    expect(within(review).getByText("Stanbic")).toBeInTheDocument();
    expect(within(review).getByRole("link", { name: "Open the proof" })).toBeInTheDocument();
    expect(within(review).getByText("Partly the same")).toBeInTheDocument();
    expect(within(review).getByText(/Approve only if you judge it is the same person/)).toBeInTheDocument();
  });

  it("checks a number against the name the network holds, with no name to type", async () => {
    const api = stub();
    const check = vi.spyOn(api, "check");
    const { user } = renderScreen({ api });
    const review = await openAccount(user, "Amara Nakato");
    expect(within(review).getByText("Registered name on the number")).toBeInTheDocument();
    expect(within(review).getByText("AMARA NAKATO", { selector: "dd" })).toBeInTheDocument();
    expect(within(review).queryByLabelText("Name on the proof")).not.toBeInTheDocument();
    await user.type(within(review).getByLabelText("Reason for your decision"), "Network name is the ID name");
    await user.click(within(review).getByRole("button", { name: "Record the check" }));
    expect(check).toHaveBeenCalledWith("sample-account-amara", expect.objectContaining({ proofName: null }));
    const updated = await screen.findByRole("article", { name: "Payout account for Amara Nakato" });
    expect(await within(updated).findByText("Checked by you")).toBeInTheDocument();
    expect(within(updated).getByText(/You made this check, so a different person must approve it/)).toBeInTheDocument();
    expect(within(updated).queryByRole("tab", { name: "Approve the check" })).not.toBeInTheDocument();
  });

  it("offers no check for a number whose network name is not read yet", async () => {
    const api = stub();
    const open = api.openAccount.bind(api);
    vi.spyOn(api, "openAccount").mockImplementation(async (id) => ({ ...(await open(id)), networkName: null }));
    const { user } = renderScreen({ api });
    const review = await openAccount(user, "Amara Nakato");
    expect(within(review).getByText(/The network has not given a name for this number yet/)).toBeInTheDocument();
    expect(within(review).queryByRole("button", { name: "Record the check" })).not.toBeInTheDocument();
    await user.click(within(review).getByRole("tab", { name: "Refuse the proof" }));
    expect(within(review).getByLabelText("Reason for your decision")).toBeInTheDocument();
  });

  it("refuses a typed name that is not the client's, and records one that is", async () => {
    const { user } = renderScreen();
    const review = await openAccount(user, "Peter Okello");
    await user.click(within(review).getByRole("tab", { name: "Check again" }));
    await user.type(within(review).getByLabelText("Name on the proof"), "Peter Atim");
    await user.type(within(review).getByLabelText("Date on the statement"), "2026-11-01");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Name on the statement");
    await user.click(within(review).getByRole("button", { name: "Check again" }));
    expect(await within(review).findByRole("alert")).toHaveTextContent("A different name can never be approved");

    await user.clear(within(review).getByLabelText("Name on the proof"));
    await user.type(within(review).getByLabelText("Name on the proof"), "Okello Peter James");
    await user.click(within(review).getByRole("button", { name: "Check again" }));
    const updated = await screen.findByRole("article", { name: "Payout account for Peter Okello" });
    expect(await within(updated).findByText("Checked by you")).toBeInTheDocument();
  });

  it("asks for the statement date on a bank statement before sending", async () => {
    const api = stub();
    const check = vi.spyOn(api, "check");
    const { user } = renderScreen({ api });
    const review = await openAccount(user, "Peter Okello");
    await user.click(within(review).getByRole("tab", { name: "Check again" }));
    await user.type(within(review).getByLabelText("Name on the proof"), "Peter James Okello");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Full name on statement");
    await user.click(within(review).getByRole("button", { name: "Check again" }));
    expect(within(review).getByText("Give the date printed on the statement.")).toBeInTheDocument();
    expect(check).not.toHaveBeenCalled();
  });

  it("approves someone else's check after asking once more", async () => {
    const { user } = renderScreen();
    const review = await openAccount(user, "Peter Okello");
    expect(within(review).getByRole("tab", { name: "Approve the check" })).toHaveAttribute("aria-selected", "true");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Middle name only differs");
    await user.click(within(review).getByRole("button", { name: "Approve the check" }));
    await user.click(within(review).getByRole("button", { name: "Yes, approve" }));
    expect(await within(await reopened("Peter Okello")).findByText(/It can be paid from/)).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Payout accounts" });
    expect(within(list).queryByText("Peter Okello")).not.toBeInTheDocument();
  });

  it("says so when the account is approved for another client", async () => {
    const { user } = renderScreen();
    const review = await openAccount(user, "Sarah Achieng");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Letter matches");
    await user.click(within(review).getByRole("button", { name: "Approve the check" }));
    await user.click(within(review).getByRole("button", { name: "Yes, approve" }));
    expect(await within(review).findByRole("alert")).toHaveTextContent("already approved for another client");
  });

  it("refuses to check an account whose client has no approved identity check", async () => {
    const { user } = renderScreen();
    const review = await openAccount(user, "David Mugisha");
    expect(within(review).getByText("No approved identity check")).toBeInTheDocument();
    await user.type(within(review).getByLabelText("Reason for your decision"), "Network name");
    await user.click(within(review).getByRole("button", { name: "Record the check" }));
    expect(await within(review).findByRole("alert")).toHaveTextContent("no approved identity check");
  });

  it("refuses proof with a reason, and asks once more for suspected fraud", async () => {
    const { user } = renderScreen();
    const review = await openAccount(user, "Amara Nakato");
    await user.click(within(review).getByRole("tab", { name: "Refuse the proof" }));
    await user.click(within(review).getByRole("button", { name: "Refuse the proof" }));
    expect(within(review).getByText("Choose why the proof is refused.")).toBeInTheDocument();
    await user.selectOptions(within(review).getByLabelText("Why the proof is refused"), "suspected_fraud");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Name registered to someone else");
    await user.click(within(review).getByRole("button", { name: "Refuse the proof" }));
    await user.click(within(review).getByRole("button", { name: "Yes, refuse" }));
    expect(await within(await reopened("Amara Nakato")).findByText(/This is final: the account can only be removed/)).toBeInTheDocument();
  });

  it("removes an account for good after asking once more", async () => {
    const { user } = renderScreen();
    const review = await openAccount(user, "Joan Namutebi");
    await user.click(within(review).getByRole("tab", { name: "Remove the account" }));
    await user.type(within(review).getByLabelText("Reason for your decision"), "Client closed the account");
    await user.click(within(review).getByRole("button", { name: "Remove the account" }));
    expect(within(review).getByRole("alertdialog", { name: "Confirm" })).toHaveTextContent("Removing is final");
    await user.click(within(review).getByRole("button", { name: "Yes, remove" }));
    expect(await within(await reopened("Joan Namutebi")).findByText(/Removed for good/)).toBeInTheDocument();
  });

  it("says nothing was done when the person does not confirm it is them", async () => {
    const api = stub();
    vi.spyOn(api, "approve").mockRejectedValue(new ReviewQueueError("not_confirmed"));
    const { user } = renderScreen({ api });
    const review = await openAccount(user, "Peter Okello");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Same person");
    await user.click(within(review).getByRole("button", { name: "Approve the check" }));
    await user.click(within(review).getByRole("button", { name: "Yes, approve" }));
    expect(await within(review).findByRole("alert")).toHaveTextContent("Not done. Confirm it is you");
  });

  it("lets staff without the permission look but not decide", async () => {
    const { user } = renderScreen({ canDecide: false });
    const review = await openAccount(user, "Peter Okello");
    expect(within(review).getByText(/Deciding it needs the payout accounts permission/)).toBeInTheDocument();
    expect(within(review).queryByRole("form", { name: "Decision" })).not.toBeInTheDocument();
  });

  it("shows an empty queue", async () => {
    renderScreen({ api: stub("empty") });
    expect(await screen.findByRole("heading", { name: "No accounts to check" })).toBeInTheDocument();
  });

  it("shows a failure with a way to try again", async () => {
    renderScreen({ api: stub("error") });
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading", { name: "Payout accounts could not be loaded" })).toBeInTheDocument();
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("review queue: held payouts", () => {
  it("opens the payout waiting longest", async () => {
    renderScreen({ entry: "/review-queue?tab=held" });
    const review = await screen.findByRole("article", { name: "Held payout for Ivan Ssemanda" });
    expect(within(review).getByRole("heading", { name: "$40,000.00" })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Held payouts" });
    expect(within(list).getAllByRole("button").map((button) => button.textContent)).toEqual([
      expect.stringContaining("$40,000.00"),
      expect.stringContaining("UGX 25,000,000"),
      expect.stringContaining("UGX 12,500,000"),
    ]);
    expect(screen.getByRole("tab", { name: "Held payouts (3)" })).toHaveAttribute("aria-selected", "true");
  });

  it("says where each payout stands: no proposal, someone else's, or your own", async () => {
    renderScreen({ entry: "/review-queue?tab=held" });
    const list = await screen.findByRole("list", { name: "Held payouts" });
    expect(await within(list).findByText("Proposed by Grace Auma, needs a second person")).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: /\$40,000\.00/ })).toHaveTextContent("Needs a proposal");
    expect(within(list).getByRole("button", { name: /UGX 12,500,000/ })).toHaveTextContent("Proposed by you");
  });

  it("proposes a release without moving money, and then waits for someone else", async () => {
    const { user } = renderScreen({ entry: "/review-queue?tab=held" });
    const review = await screen.findByRole("article", { name: "Held payout for Ivan Ssemanda" });
    await user.click(within(review).getByRole("button", { name: "Propose release" }));
    expect(within(review).getByText("Give the reason for your decision.")).toBeInTheDocument();
    await user.type(within(review).getByLabelText("Reason for your decision"), "Client confirmed by phone");
    await user.click(within(review).getByRole("button", { name: "Propose release" }));
    const updated = await screen.findByRole("article", { name: "Held payout for Ivan Ssemanda" });
    expect(await within(updated).findByText(/You proposed releasing this payout/)).toBeInTheDocument();
    expect(within(updated).getByText("Client confirmed by phone")).toBeInTheDocument();
    expect(within(updated).queryByRole("button", { name: "Approve release" })).not.toBeInTheDocument();
    expect(within(updated).getByRole("button", { name: "Withdraw proposal" })).toBeInTheDocument();
  });

  it("approves someone else's proposal after asking once more", async () => {
    const { user } = renderScreen({ entry: "/review-queue?tab=held&payout=sample-payout-joan" });
    const review = await screen.findByRole("article", { name: "Held payout for Joan Namutebi" });
    expect(within(review).getByText(/Called the client on the number we hold/)).toBeInTheDocument();
    await user.type(within(review).getByLabelText("Reason for your decision"), "Agree, amount matches her request");
    await user.click(within(review).getByRole("button", { name: "Approve release" }));
    await user.click(within(review).getByRole("button", { name: "Yes, approve" }));
    expect(await within(review).findByText(/queued for staff to pay from the trust account/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Held payouts (2)" })).toBeInTheDocument();
  });

  it("withdraws your own proposal", async () => {
    const { user } = renderScreen({ entry: "/review-queue?tab=held&payout=sample-payout-kira" });
    const review = await screen.findByRole("article", { name: "Held payout for Kira Road Savings Group" });
    await user.type(within(review).getByLabelText("Reason for your decision"), "Proposed on the wrong payout");
    await user.click(within(review).getByRole("button", { name: "Withdraw proposal" }));
    const updated = await screen.findByRole("article", { name: "Held payout for Kira Road Savings Group" });
    expect(await within(updated).findByRole("button", { name: "Propose release" })).toBeInTheDocument();
  });

  it("keeps the key for a retried decision and makes a new one when the reason changes", async () => {
    const api = stub();
    const decline = vi
      .spyOn(api, "decline")
      .mockRejectedValueOnce(new ReviewQueueError("unavailable"))
      .mockRejectedValueOnce(new ReviewQueueError("unavailable"));
    let keys = 0;
    const { user } = renderScreen({ api, entry: "/review-queue?tab=held", newKey: () => `key-${++keys}` });
    const review = await screen.findByRole("article", { name: "Held payout for Ivan Ssemanda" });
    const reason = within(review).getByLabelText("Reason for your decision");
    await user.type(reason, "Client called");
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await user.click(within(review).getByRole("button", { name: "Decline" }));
      await user.click(within(review).getByRole("button", { name: "Yes, decline" }));
      expect(await within(review).findByRole("alert")).toHaveTextContent("could not reach Finvest");
    }
    await user.type(reason, " twice");
    await user.click(within(review).getByRole("button", { name: "Decline" }));
    await user.click(within(review).getByRole("button", { name: "Yes, decline" }));
    expect(await within(review).findByText(/money went back where it came from/)).toBeInTheDocument();
    expect(decline.mock.calls.map((call) => call[2])).toEqual(["key-1", "key-1", "key-2"]);
  });

  it("reloads when the proposal was already decided", async () => {
    const api = stub();
    vi.spyOn(api, "approveRelease").mockRejectedValueOnce(new ReviewQueueError("release_already_decided"));
    const list = vi.spyOn(api, "listHeld");
    const { user } = renderScreen({ api, entry: "/review-queue?tab=held&payout=sample-payout-joan" });
    const review = await screen.findByRole("article", { name: "Held payout for Joan Namutebi" });
    await user.type(within(review).getByLabelText("Reason for your decision"), "Agree");
    await user.click(within(review).getByRole("button", { name: "Approve release" }));
    await user.click(within(review).getByRole("button", { name: "Yes, approve" }));
    expect(await within(review).findByRole("alert")).toHaveTextContent("already been decided or withdrawn");
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("refuses to let you approve your own proposal in the sample, as the backend does", async () => {
    const api = stub();
    await expect(api.approveRelease("sample-release-kira", "Me again", "key-x")).rejects.toMatchObject({ code: "proposed_by_you" });
  });

  it("shows nothing held", async () => {
    renderScreen({ api: stub("empty"), entry: "/review-queue?tab=held" });
    expect(await screen.findByRole("heading", { name: "No payouts held" })).toBeInTheDocument();
  });
});

describe("the sample name lock", () => {
  it.each([
    ["Nakato Amara", "AMARA NAKATO", "exact"],
    ["Peter Okello", "OKELLO PETER JAMES", "partial"],
    ["Amara Atim", "AMARA NAKATO", "different"],
    ["Okello", "OKELLO PETER", "different"],
  ])("%s against %s is %s", (proof, id, match) => {
    expect(compareNames(proof, id)).toBe(match);
  });
});
