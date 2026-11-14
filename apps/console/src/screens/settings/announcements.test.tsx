import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { AnnouncementsSection, reachSentence } from "./Announcements.tsx";
import { AnnouncementsError, checkAnnouncement, notConnectedAnnouncementsApi, type AnnouncementsApi } from "./announcements.ts";
import { createStubAnnouncementsApi } from "./announcementsStub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };
const NOW = Date.parse("2026-11-13T09:00:00Z");

function renderSection({
  api = createStubAnnouncementsApi(ME, () => NOW),
  canChange = true,
}: { api?: AnnouncementsApi; canChange?: boolean } = {}) {
  render(<AnnouncementsSection api={api} directory={createStubDirectory(ME)} canChange={canChange} />);
  return { api, user: userEvent.setup() };
}

const card = (name: string) => screen.findByRole("article", { name });

describe("reachSentence", () => {
  it("names one client and says when nobody chose SMS or email", () => {
    expect(reachSentence({ clients: 1, sms: 0, email: 0 })).toBe(
      "Approving sends it straight away to 1 active client, in the app and by push. None of them chose SMS or email for news.",
    );
  });
});

describe("settings: announcements", () => {
  it("lists the latest first, with the one waiting for a second person", async () => {
    renderSection();
    const waiting = await card("Office closed on Monday");
    expect(screen.getByText(/1 is waiting for a second person/)).toBeInTheDocument();
    expect(waiting).toHaveTextContent("Waiting for a second person");
    expect(waiting).toHaveTextContent("Proposed by Daniel Kato, 13 Nov, 10:10. Public holiday");
    const sent = await card("New 364 day Treasury bill");
    expect(sent).toHaveTextContent("Sent to 1,284 clients");
    expect(sent).toHaveTextContent("Approved by Daniel Kato, 3 Nov, 12:30");
    expect(screen.getAllByRole("article").map((item) => item.getAttribute("aria-label"))).toEqual([
      "Office closed on Monday",
      "New 364 day Treasury bill",
    ]);
  });

  it("approves someone else's announcement, which sends it", async () => {
    const { user } = renderSection();
    const waiting = await card("Office closed on Monday");
    expect(waiting).toHaveTextContent(
      "Approving sends it straight away to 1,284 active clients, in the app and by push. 96 of them also get it by SMS and 310 by email, as they chose for news. It cannot be called back.",
    );
    await user.click(within(waiting).getByRole("button", { name: "Approve and send" }));
    await waitFor(async () => expect(await card("Office closed on Monday")).toHaveTextContent("Sent to 1,284 clients"));
    expect(await card("Office closed on Monday")).toHaveTextContent("Approved by Amina Kintu, 13 Nov, 12:00");
    expect(screen.getByText(/None is waiting/)).toBeInTheDocument();
  });

  it("needs a reason to reject", async () => {
    const { user } = renderSection();
    const waiting = await card("Office closed on Monday");
    await user.click(within(waiting).getByRole("button", { name: "Reject" }));
    const form = within(waiting).getByRole("form", { name: "Reject the announcement" });
    await user.click(within(form).getByRole("button", { name: "Reject it" }));
    expect(within(form).getByText("Say why, so the proposer knows what to change.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText("What needs to change?"), "Say the office reopens on Tuesday.");
    await user.click(within(form).getByRole("button", { name: "Reject it" }));
    const after = await card("Office closed on Monday");
    await waitFor(() => expect(after).toHaveTextContent("Rejected"));
    expect(after).toHaveTextContent("Rejected by Amina Kintu, 13 Nov, 12:00. Say the office reopens on Tuesday.");
  });

  it("proposes one, checks the words as they are typed, and lets the proposer only withdraw it", async () => {
    const { user } = renderSection();
    await card("Office closed on Monday");
    await user.click(screen.getByRole("button", { name: "New announcement" }));
    const form = screen.getByRole("form", { name: "New announcement" });
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getAllByText("Write something here.")).toHaveLength(3);
    await user.type(within(form).getByLabelText(/^Title/), "Rates up 1% this week");
    expect(within(form).getByText("Remove the percent sign. Clients see net amounts, never a rate.")).toBeInTheDocument();
    await user.clear(within(form).getByLabelText(/^Title/));
    await user.type(within(form).getByLabelText(/^Title/), "App update");
    await user.type(within(form).getByLabelText(/^Message/), "Read more at example.com/update");
    expect(within(form).getByText("Link only to finvestug.com.")).toBeInTheDocument();
    await user.clear(within(form).getByLabelText(/^Message/));
    await user.type(within(form).getByLabelText(/^Message/), "Update the app to see your goals on the home screen.");
    await user.type(within(form).getByLabelText(/^Why send it/), "New release");
    expect(within(form).getByRole("figure", { name: "As a client sees it" })).toHaveTextContent(
      "SMS: Finvest: Update the app to see your goals on the home screen.",
    );
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const mine = await card("App update");
    expect(screen.queryByRole("form", { name: "New announcement" })).not.toBeInTheDocument();
    expect(mine).toHaveTextContent("Proposed by you, 13 Nov, 12:00. New release");
    expect(within(mine).queryByRole("button", { name: "Approve and send" })).not.toBeInTheDocument();
    await user.click(within(mine).getByRole("button", { name: "Withdraw it" }));
    await waitFor(async () => expect(await card("App update")).toHaveTextContent("Withdrawn by you, 13 Nov, 12:00"));
  });

  it("says only that it goes to every active client when the counts cannot be read", async () => {
    const stub = createStubAnnouncementsApi(ME, () => NOW);
    renderSection({ api: { ...stub, reach: () => Promise.reject(new AnnouncementsError("unavailable")) } });
    const waiting = await card("Office closed on Monday");
    expect(waiting).toHaveTextContent("Approving sends it to every active client straight away. It cannot be called back.");
  });

  it("shows a role that cannot send announcements who approves", async () => {
    renderSection({ canChange: false });
    const waiting = await card("Office closed on Monday");
    expect(within(waiting).queryByRole("button")).not.toBeInTheDocument();
    expect(waiting).toHaveTextContent("Someone who can send announcements approves it.");
    expect(screen.queryByRole("button", { name: "New announcement" })).not.toBeInTheDocument();
  });

  it("shows an empty list", async () => {
    renderSection({ api: createStubAnnouncementsApi(ME, () => NOW, "empty") });
    expect(await screen.findByRole("heading", { name: "No announcements yet" })).toBeInTheDocument();
  });

  it("says when announcements are not connected, and tries again", async () => {
    const { user } = renderSection({ api: notConnectedAnnouncementsApi });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Announcements could not be loaded");
    expect(alert).toHaveTextContent("Announcements are not connected to the Finvest service yet.");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("shows what the stub refuses as the backend would", async () => {
    const api = createStubAnnouncementsApi(ME, () => NOW);
    await expect(api.decide("sample-announcement-2", "withdrawn", null)).rejects.toMatchObject({ code: "forbidden" });
    await expect(api.decide("sample-announcement-2", "rejected", " ")).rejects.toMatchObject({ code: "invalid" });
    await expect(api.decide("sample-announcement-1", "approved", null)).rejects.toMatchObject({ code: "not_found" });
  });
});

describe("announcement words", () => {
  it.each([
    [{ title: "Hello {name}", message: "Hi", reason: "R" }, [{ field: "title", code: "stray_brace" }]],
    [{ title: "T", message: "Karibu ✓", reason: "R" }, [{ field: "message", code: "invalid_characters" }]],
    [{ title: "T", message: "x".repeat(292), reason: "R" }, [{ field: "message", code: "too_long" }]],
    [{ title: "T", message: "See https://finvestug.com/help", reason: "R" }, []],
    [{ title: "T", message: "M", reason: " " }, [{ field: "reason", code: "required" }]],
  ])("checks %o", (input, expected) => {
    expect(checkAnnouncement(input)).toEqual(expected);
  });
});
