import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { CorrectionsError, notConnectedCorrectionsApi, type CorrectionsApi } from "./api.ts";
import { CorrectionsSection } from "./Corrections.tsx";
import { createStubCorrectionsApi } from "./stub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };
const NOW = "2026-11-13T09:00:00Z";

function renderSection({
  api = createStubCorrectionsApi(ME.id, "default", () => NOW),
  canChange = true,
}: { api?: CorrectionsApi; canChange?: boolean } = {}) {
  let n = 0;
  render(
    <CorrectionsSection api={api} directory={createStubDirectory(ME)} me={ME.id} canChange={canChange} newKey={() => `key-${++n}`} />,
  );
  return { api, user: userEvent.setup() };
}

const card = (name: string) => screen.findByRole("article", { name });
const UNWIND = "Undo an investment, UGX 1,000,000";
const MY_REFUND = "Refund to the client, UGX 500,000";

describe("investments: corrections", () => {
  it("lists them newest first, with who proposed and decided each", async () => {
    renderSection();
    const waiting = await card(UNWIND);
    expect(screen.getByText(/2 are waiting for a second person/)).toBeInTheDocument();
    expect(waiting).toHaveTextContent("Proposed by Grace Auma");
    expect(waiting).toHaveTextContent("matched to the wrong client");
    const failed = await card("Refund to the client, UGX 9,000,000");
    expect(failed).toHaveTextContent("Nothing moved");
    expect(failed).toHaveTextContent("over what one mobile money payout can carry");
    const back = await card("Undo an investment, UGX 12,000,000");
    expect(back).toHaveTextContent("Sent back by you");
    expect(back).toHaveTextContent("two separate transfers");
  });

  it("approves someone else's, and Finvest moves the money", async () => {
    const { user } = renderSection();
    const waiting = await card(UNWIND);
    expect(waiting).toHaveTextContent("the client stops seeing it");
    await user.click(within(waiting).getByRole("button", { name: "Approve" }));
    await waitFor(async () => expect(await card(UNWIND)).toHaveTextContent("Done"));
    expect(await card(UNWIND)).toHaveTextContent("Approved by you");
    expect(screen.getByText(/1 is waiting for a second person/)).toBeInTheDocument();
  });

  it("lets the proposer only withdraw their own", async () => {
    const { user } = renderSection();
    const mine = await card(MY_REFUND);
    expect(within(mine).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(mine).toHaveTextContent("Someone else approves it.");
    await user.click(within(mine).getByRole("button", { name: "Withdraw it" }));
    await waitFor(async () => expect(await card(MY_REFUND)).toHaveTextContent("Withdrawn by you"));
  });

  it("needs a note to send one back", async () => {
    const { user } = renderSection();
    const waiting = await card(UNWIND);
    await user.click(within(waiting).getByRole("button", { name: "Send back" }));
    const form = within(waiting).getByRole("form", { name: "Send the correction back" });
    await user.click(within(form).getByRole("button", { name: "Send it back" }));
    expect(within(form).getByText("Say why, so the proposer knows what to check.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText("What needs checking?"), "Attach the bank statement line.");
    await user.click(within(form).getByRole("button", { name: "Send it back" }));
    await waitFor(async () => expect(await card(UNWIND)).toHaveTextContent("Sent back by you"));
    expect(await card(UNWIND)).toHaveTextContent("Attach the bank statement line.");
  });

  it("proposes an unwind, and a refund only with a payout account", async () => {
    const { user } = renderSection();
    await user.click(await screen.findByRole("button", { name: "Propose a correction" }));
    const form = screen.getByRole("form", { name: "Propose a correction" });
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getAllByText("Fill this in.")).toHaveLength(2);

    await user.click(within(form).getByRole("radio", { name: /Refund money waiting to start/ }));
    expect(form).toHaveTextContent("never with a refund here");
    expect(within(form).getByLabelText(/Payout account ID/)).toBeInTheDocument();
    await user.click(within(form).getByRole("radio", { name: /Undo a running investment/ }));
    expect(within(form).queryByLabelText(/Payout account ID/)).toBeNull();

    await user.type(within(form).getByLabelText(/Investment ID/), "-InvSample00000000A1");
    await user.type(within(form).getByLabelText(/Why\?/), "Deposit matched to the wrong client.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const created = await card("Undo an investment, UGX 5,000,000");
    expect(created).toHaveTextContent("Waiting for a second person");
    expect(created).toHaveTextContent("Proposed by you");
    expect(screen.queryByRole("form", { name: "Propose a correction" })).toBeNull();
  });

  it("marks what Finvest refused on the form", async () => {
    const api = createStubCorrectionsApi(ME.id, "empty");
    api.propose = () => Promise.reject(new CorrectionsError("invalid", [{ field: "investment_id", code: "carried_over" }]));
    const { user } = renderSection({ api });
    await user.click(await screen.findByRole("button", { name: "Propose a correction" }));
    const form = screen.getByRole("form", { name: "Propose a correction" });
    await user.type(within(form).getByLabelText(/Investment ID/), "-InvCarried000000001");
    await user.type(within(form).getByLabelText(/Why\?/), "Wrong amount.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(await within(form).findByText(/came over from Finvest 1.0/)).toBeInTheDocument();
    expect(within(form).getByRole("alert")).toHaveTextContent("Fix what is marked");
  });

  it("sends the same key again when the answer was lost", async () => {
    const base = createStubCorrectionsApi(ME.id, "default", () => NOW);
    const approve = vi
      .fn<CorrectionsApi["approve"]>()
      .mockRejectedValueOnce(new CorrectionsError("unavailable"))
      .mockImplementation((key, id) => base.approve(key, id));
    const { user } = renderSection({ api: { ...base, approve } });
    const waiting = await card(UNWIND);
    await user.click(within(waiting).getByRole("button", { name: "Approve" }));
    expect(await within(await card(UNWIND)).findByRole("alert")).toHaveTextContent("could not reach Finvest");
    await user.click(within(await card(UNWIND)).getByRole("button", { name: "Approve" }));
    await waitFor(async () => expect(await card(UNWIND)).toHaveTextContent("Done"));
    expect(approve.mock.calls.map(([key]) => key)).toEqual(["key-1", "key-1"]);
  });

  it("says when the person did not confirm it is them", async () => {
    const base = createStubCorrectionsApi(ME.id, "default", () => NOW);
    const { user } = renderSection({ api: { ...base, approve: () => Promise.reject(new CorrectionsError("not_confirmed")) } });
    const waiting = await card(UNWIND);
    await user.click(within(waiting).getByRole("button", { name: "Approve" }));
    expect(await within(await card(UNWIND)).findByRole("alert")).toHaveTextContent("Confirm it is you");
  });

  it("only reads without the permission", async () => {
    renderSection({ canChange: false });
    const waiting = await card(UNWIND);
    expect(screen.queryByRole("button", { name: "Propose a correction" })).toBeNull();
    expect(within(waiting).queryByRole("button")).toBeNull();
    expect(waiting).toHaveTextContent("Someone who can correct investments approves it.");
  });

  it("shows an empty system and one that cannot reach Finvest", async () => {
    const { unmount } = render(
      <CorrectionsSection api={createStubCorrectionsApi(ME.id, "empty")} directory={createStubDirectory(ME)} me={ME.id} canChange />,
    );
    expect(await screen.findByText("Nothing has been corrected")).toBeInTheDocument();
    unmount();
    render(<CorrectionsSection api={notConnectedCorrectionsApi} directory={createStubDirectory(ME)} me={ME.id} canChange />);
    expect(await screen.findByText("Corrections could not be loaded")).toBeInTheDocument();
    expect(screen.getByText(/not connected to the Finvest service/)).toBeInTheDocument();
  });
});
