import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { notConnectedRunRequestsApi, RunRequestsError, type RunKind, type RunRequestsApi } from "./api.ts";
import { RunAgain } from "./RunAgain.tsx";
import { createStubRunRequestsApi } from "./stub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };
const TODAY = "2026-11-14";

function renderRuns({
  kind = "interest",
  api = createStubRunRequestsApi(ME.id, "default", () => TODAY, () => `${TODAY}T09:00:00Z`),
  canChange = true,
}: { kind?: RunKind; api?: RunRequestsApi; canChange?: boolean } = {}) {
  let n = 0;
  render(<RunAgain api={api} directory={createStubDirectory(ME)} kind={kind} me={ME.id} canChange={canChange} today={TODAY} newKey={() => `key-${++n}`} />);
  return { api, user: userEvent.setup() };
}

describe("investments: running a day again", () => {
  it("lists the interest requests, open ones first, with who asked", async () => {
    renderRuns();
    const open = await screen.findByRole("article", { name: "Interest run for 13 Nov 2026" });
    expect(open).toHaveTextContent("Waiting for a second person");
    await waitFor(() => expect(open).toHaveTextContent("Grace Auma"));
    expect(screen.getByRole("article", { name: "Interest run for 10 Nov 2026" })).toHaveTextContent("Approved, run queued");
    expect(screen.queryByRole("article", { name: /Maturity run/ })).toBeNull();
  });

  it("approves someone else's request, which queues the run", async () => {
    const { user } = renderRuns();
    const open = await screen.findByRole("article", { name: "Interest run for 13 Nov 2026" });
    expect(open).toHaveTextContent("It posts only what is still owed");
    await user.click(within(open).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByRole("article", { name: "Interest run for 13 Nov 2026" })).toHaveTextContent("Approved by you"));
  });

  it("asks for a day again with a reason, for someone else to approve", async () => {
    const { user } = renderRuns();
    await user.click(await screen.findByRole("button", { name: "Run again for a date" }));
    const form = screen.getByRole("form", { name: "Ask for an interest run again" });
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(form).toHaveTextContent("Fill this in.");
    const day = within(form).getByLabelText(/Day to run again/);
    await user.clear(day);
    await user.type(day, "2026-11-12");
    await user.type(within(form).getByLabelText(/Why\?/), "The run stopped part way.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const mine = await screen.findByRole("article", { name: "Interest run for 12 Nov 2026" });
    expect(mine).toHaveTextContent("Asked by you");
    expect(within(mine).getByRole("button", { name: "Withdraw it" })).toBeInTheDocument();
  });

  it("shows the backend's refusal of a day beside the date", async () => {
    const propose = vi.fn<RunRequestsApi["propose"]>().mockRejectedValue(new RunRequestsError("invalid", [{ field: "run_date", code: "too_old" }]));
    const api = { ...createStubRunRequestsApi(ME.id, "empty", () => TODAY), propose };
    const { user } = renderRuns({ api });
    await user.click(await screen.findByRole("button", { name: "Run again for a date" }));
    const form = screen.getByRole("form", { name: "Ask for an interest run again" });
    await user.type(within(form).getByLabelText(/Why\?/), "Missed day.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(await within(form).findByText("Only the last 31 days can be run again.")).toBeInTheDocument();
    expect(propose).toHaveBeenCalledWith("key-1", { kind: "interest", runDate: "2026-11-13", reason: "Missed day." });
  });

  it("asks for today's maturity run only, and lets the asker withdraw it", async () => {
    const { user } = renderRuns({ kind: "maturity" });
    const mine = await screen.findByRole("article", { name: "Maturity run for 14 Nov 2026" });
    expect(within(mine).queryByRole("button", { name: "Approve" })).toBeNull();
    await user.click(within(mine).getByRole("button", { name: "Withdraw it" }));
    await waitFor(() => expect(screen.getByRole("article", { name: "Maturity run for 14 Nov 2026" })).toHaveTextContent("Withdrawn by you"));
    await user.click(screen.getByRole("button", { name: "Run today again" }));
    const form = screen.getByRole("form", { name: "Ask for today's maturity run again" });
    expect(form).toHaveTextContent("For 14 Nov 2026, today.");
    expect(within(form).queryByLabelText(/Day to run again/)).toBeNull();
  });

  it("only reads for someone who cannot correct investments", async () => {
    renderRuns({ canChange: false });
    const open = await screen.findByRole("article", { name: "Interest run for 13 Nov 2026" });
    expect(open).toHaveTextContent("Someone who can correct investments approves it.");
    expect(screen.queryByRole("button", { name: "Run again for a date" })).toBeNull();
  });

  it("says so when it cannot reach Finvest", async () => {
    renderRuns({ api: notConnectedRunRequestsApi });
    expect(await screen.findByRole("alert")).toHaveTextContent("Running again is not connected");
  });

  it("says when nothing was asked for again", async () => {
    renderRuns({ api: createStubRunRequestsApi(ME.id, "empty", () => TODAY) });
    expect(await screen.findByText("No interest run has been asked for again.")).toBeInTheDocument();
  });
});
