import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { notConnectedReissuesApi, type ReissuesApi } from "./api.ts";
import { Reissues } from "./Reissues.tsx";
import { createStubReissuesApi } from "./stub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };
const TODAY = "2026-11-14";

function renderReissues({
  api = createStubReissuesApi(ME.id, "default", () => TODAY, () => `${TODAY}T09:00:00Z`),
  canChange = true,
}: { api?: ReissuesApi; canChange?: boolean } = {}) {
  let n = 0;
  render(<Reissues api={api} directory={createStubDirectory(ME)} me={ME.id} canChange={canChange} today={TODAY} newKey={() => `key-${++n}`} />);
  return { api, user: userEvent.setup() };
}

describe("investments: reissuing transaction notes", () => {
  it("lists requests, open ones first, with whose notes and how they went", async () => {
    renderReissues();
    const open = await screen.findByRole("article", { name: /Notes issued 15 Oct 2026 to 14 Nov 2026, for/ });
    expect(open).toHaveTextContent("Waiting for a second person");
    await waitFor(() => expect(open).toHaveAccessibleName("Notes issued 15 Oct 2026 to 14 Nov 2026, for Joan Namutebi"));
    const done = screen.getByRole("article", { name: "Notes issued 12 Nov 2026, for every client" });
    expect(done).toHaveTextContent("Sent again");
    expect(done).toHaveTextContent("35 sent; 2 not sent");
  });

  it("approves someone else's request, which sends the notes", async () => {
    const { user } = renderReissues();
    const open = await screen.findByRole("article", { name: /Notes issued 15 Oct 2026/ });
    await user.click(within(open).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByRole("article", { name: /Notes issued 15 Oct 2026/ })).toHaveTextContent("3 sent."));
  });

  it("asks for every client's notes of a day, showing how many first", async () => {
    const { user } = renderReissues();
    await user.click(await screen.findByRole("button", { name: "Reissue notes" }));
    const form = screen.getByRole("form", { name: "Reissue transaction notes" });
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(form).toHaveTextContent("Fill this in.");
    await user.click(within(form).getByLabelText("Every client"));
    const from = within(form).getByLabelText(/Notes issued from/);
    await user.clear(from);
    await user.type(from, "2026-11-13");
    expect(await within(form).findByText("80 notes would be emailed again.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText(/Why\?/), "Resend was down for an hour.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const mine = await screen.findByRole("article", { name: "Notes issued 13 Nov 2026 to 14 Nov 2026, for every client" });
    expect(mine).toHaveTextContent("80 notes");
    expect(within(mine).getByRole("button", { name: "Withdraw it" })).toBeInTheDocument();
  });

  it("asks for one client's notes and refuses a range with none", async () => {
    const { user } = renderReissues({ api: createStubReissuesApi(ME.id, "empty", () => TODAY) });
    await user.click(await screen.findByRole("button", { name: "Reissue notes" }));
    const form = screen.getByRole("form", { name: "Reissue transaction notes" });
    expect(form).toHaveTextContent("Choose the client to see how many notes this covers.");
    await user.type(within(form).getByLabelText("Client"), "Kira");
    await user.click(await within(form).findByRole("button", { name: /Kira Road/ }));
    expect(await within(form).findByText("No notes were issued on these days, so there is nothing to send.")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Send for approval" })).toBeDisabled();
    await user.click(within(form).getByRole("button", { name: "Change" }));
    await user.clear(within(form).getByLabelText("Client"));
    await user.type(within(form).getByLabelText("Client"), "Joan");
    await user.click(await within(form).findByRole("button", { name: /Joan Namutebi/ }));
    expect(await within(form).findByText("3 notes would be emailed again.")).toBeInTheDocument();
  });

  it("refuses more than 31 days before asking Finvest", async () => {
    const { user } = renderReissues();
    await user.click(await screen.findByRole("button", { name: "Reissue notes" }));
    const form = screen.getByRole("form", { name: "Reissue transaction notes" });
    const from = within(form).getByLabelText(/Notes issued from/);
    await user.clear(from);
    await user.type(from, "2026-09-01");
    expect(within(form).getByText("Choose at most 31 days.")).toBeInTheDocument();
  });

  it("only reads for someone who cannot send documents", async () => {
    renderReissues({ canChange: false });
    const open = await screen.findByRole("article", { name: /Notes issued 15 Oct 2026/ });
    expect(open).toHaveTextContent("Someone who can send documents to clients approves it.");
    expect(screen.queryByRole("button", { name: "Reissue notes" })).toBeNull();
  });

  it("says so when it cannot reach Finvest", async () => {
    renderReissues({ api: notConnectedReissuesApi });
    expect(await screen.findByRole("alert")).toHaveTextContent("Reissuing notes is not connected");
  });

  it("says when no notes were sent again", async () => {
    renderReissues({ api: createStubReissuesApi(ME.id, "empty", () => TODAY) });
    expect(await screen.findByText("No notes have been sent again.")).toBeInTheDocument();
  });
});
