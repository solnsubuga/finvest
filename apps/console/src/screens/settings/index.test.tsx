import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { notConnectedWordingsApi, WordingsError, type WordingsApi } from "./api.ts";
import { SettingsScreen } from "./index.tsx";
import { checkWording, fillSample, outsideLinks } from "./rules.ts";
import { createStubAnnouncementsApi } from "./announcementsStub.ts";
import { createStubWordingsApi } from "./stub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };
const NOW = Date.parse("2026-11-13T09:00:00Z");

function renderSettings({ api = createStubWordingsApi(ME, () => NOW), canChange = true }: { api?: WordingsApi; canChange?: boolean } = {}) {
  render(
    <SettingsScreen
      api={api}
      announcements={createStubAnnouncementsApi(ME, () => NOW)}
      directory={createStubDirectory(ME)}
      canChange={canChange}
    />,
  );
  return { api, user: userEvent.setup() };
}

const panel = (name: string) => screen.findByRole("region", { name });
const messages = () => screen.getByRole("navigation", { name: "Messages" });

async function openMessage(user: ReturnType<typeof userEvent.setup>, name: string) {
  await panel("Investment matures soon");
  await user.click(within(messages()).getByRole("button", { name: new RegExp(name) }));
  return panel(name);
}

describe("settings: client messages", () => {
  it("opens the change waiting for a second person first", async () => {
    renderSettings();
    const detail = await panel("Investment matures soon");
    expect(screen.getByText(/1 change is waiting for a second person/)).toBeInTheDocument();
    expect(within(detail).getByText(/Proposed by Daniel Kato, 12 Nov, 12:30/)).toBeInTheDocument();
    const proposed = within(detail).getByRole("figure", { name: "The proposed words" });
    expect(proposed).toHaveTextContent("Your investment matures on 20 Nov 2026");
    expect(within(messages()).getByRole("button", { name: /Investment matures soon/ })).toHaveTextContent("Waiting");
    // Grouped as the client app groups its settings, with sign-in codes left out.
    expect(within(messages()).getAllByRole("heading").map((heading) => heading.textContent)).toEqual([
      "Money in and out",
      "Investments",
      "Earnings",
      "Your account",
      "Security",
      "Help and complaints",
    ]);
    expect(within(messages()).getAllByRole("button")).toHaveLength(21);
  });

  it("approves someone else's change with a reason, and clients then see it", async () => {
    const { user } = renderSettings();
    const detail = await panel("Investment matures soon");
    await user.click(within(detail).getByRole("button", { name: "Approve" }));
    await user.click(within(detail).getByRole("button", { name: "Approve the change" }));
    expect(within(detail).getByText("Write a short reason for the audit trail.")).toBeInTheDocument();
    await user.type(within(detail).getByLabelText("Why approve it?"), "Reads better.");
    await user.click(within(detail).getByRole("button", { name: "Approve the change" }));
    const after = await panel("Investment matures soon");
    await waitFor(() =>
      expect(within(after).getByRole("figure", { name: "What clients see now" })).toHaveTextContent("Your investment matures on 20 Nov 2026"),
    );
    expect(within(after).getByText("Changed 13 Nov, 12:00")).toBeInTheDocument();
    expect(within(messages()).getByRole("button", { name: /Investment matures soon/ })).toHaveTextContent("Edited");
    expect(screen.getByText(/No change is waiting/)).toBeInTheDocument();
  });

  it("checks new words as they are typed and sends them for approval", async () => {
    const { user } = renderSettings();
    const detail = await openMessage(user, "Payment received");
    await user.click(within(detail).getByRole("button", { name: "Change the words" }));
    const form = within(detail).getByRole("form", { name: "Change the words" });
    const body = within(form).getByLabelText(/^Message/);
    await user.clear(body);
    await user.type(body, "We received {{amount} at 10% from {{bank}. See evil.example/pay");
    expect(within(form).getByText("Use only {amount}, {method}.")).toBeInTheDocument();
    expect(within(form).getByText("Link only to finvestug.com.")).toBeInTheDocument();
    expect(within(form).getByText(/Remove the percent sign/)).toBeInTheDocument();
    await user.clear(body);
    await user.type(body, "We have your {{amount} from {{method}. Track it at finvestug.com/app");
    expect(within(form).queryByText(/Use only/)).not.toBeInTheDocument();
    expect(within(form).getByRole("figure", { name: "How the new words read" })).toHaveTextContent(
      "We have your UGX 1,000,000 from MTN Mobile Money.",
    );
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getByText("Say why, for the second person.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText(/Why change them/), "Clients asked where to track it.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const after = await panel("Payment received");
    expect(await within(after).findByText(/Proposed by you, 13 Nov, 12:00/)).toBeInTheDocument();
    // The proposer cannot decide their own change.
    expect(within(after).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    await user.click(within(after).getByRole("button", { name: "Withdraw it" }));
    await user.type(within(after).getByLabelText("Why withdraw it?"), "Wrong message.");
    await user.click(within(after).getByRole("button", { name: "Withdraw the change" }));
    await waitFor(() => expect(within(after).queryByText(/A change is waiting/)).not.toBeInTheDocument());
  });

  it("will not send words that have not changed", async () => {
    const { user } = renderSettings();
    const detail = await openMessage(user, "Password changed");
    expect(within(detail).getByText("This message has no values to fill in.")).toBeInTheDocument();
    await user.click(within(detail).getByRole("button", { name: "Change the words" }));
    await user.type(within(detail).getByLabelText(/Why change them/), "Testing.");
    await user.click(within(detail).getByRole("button", { name: "Send for approval" }));
    expect(within(detail).getByText(/These are the words in use now/)).toBeInTheDocument();
  });

  it("shows what the backend refused", async () => {
    const stub = createStubWordingsApi(ME, () => NOW);
    const api: WordingsApi = {
      ...stub,
      propose: () => Promise.reject(new WordingsError("invalid", [{ field: "sms", code: "invalid_characters" }])),
    };
    const { user } = renderSettings({ api });
    const detail = await openMessage(user, "Withdrawal sent");
    await user.click(within(detail).getByRole("button", { name: "Change the words" }));
    await user.type(within(detail).getByLabelText(/^Title/), " now");
    await user.type(within(detail).getByLabelText(/Why change them/), "Shorter.");
    await user.click(within(detail).getByRole("button", { name: "Send for approval" }));
    expect(await within(detail).findByRole("alert")).toHaveTextContent("Finvest refused these words.");
    expect(within(detail).getByText(/Use plain letters, digits and punctuation/)).toBeInTheDocument();
  });

  it("lets a role without wordings.change read but not change", async () => {
    renderSettings({ canChange: false });
    const detail = await panel("Investment matures soon");
    expect(within(detail).queryByRole("button")).not.toBeInTheDocument();
    expect(within(detail).getByText(/Changing them needs an admin/)).toBeInTheDocument();
  });

  it("says when client messages are not connected", async () => {
    renderSettings({ api: notConnectedWordingsApi });
    expect(await screen.findByRole("alert")).toHaveTextContent("Client messages are not connected to the Finvest service yet.");
  });
});

describe("wording rules", () => {
  const vars = ["amount", "method"];
  const ok = { title: "Payment received", body: "We received {amount} from {method}.", sms: "Finvest: we received {amount}." };

  it("passes the shipped words", () => {
    expect(checkWording(ok, vars)).toEqual([]);
  });
  it.each([
    [{ ...ok, title: "" }, "title", "required"],
    [{ ...ok, title: "x".repeat(61) }, "title", "too_long"],
    [{ ...ok, body: " leading space" }, "body", "invalid_characters"],
    [{ ...ok, body: "Two\nlines" }, "body", "invalid_characters"],
    [{ ...ok, body: "{fee} charged" }, "body", "unknown_placeholder"],
    [{ ...ok, body: "{amount} and {amount}" }, "body", "repeated_placeholder"],
    [{ ...ok, body: "A {stray brace" }, "body", "stray_brace"],
    [{ ...ok, body: "See https://evil.example/finvest" }, "body", "outside_link"],
    [{ ...ok, body: "Earn 12% a year" }, "body", "no_rates"],
    [{ ...ok, sms: "Finvest: payé" }, "sms", "invalid_characters"],
  ])("refuses %j", (wording, field, code) => {
    expect(checkWording(wording, vars)).toContainEqual({ field, code });
  });
  it("allows Finvest's own site, with or without a scheme", () => {
    expect(outsideLinks("Go to finvestug.com, https://app.finvestug.com/help or www.finvestug.com.")).toEqual([]);
    expect(outsideLinks("Go to finvestug.com.evil.example or https://finvestug.com@evil.example")).toEqual([
      "finvestug.com.evil.example",
      "https://finvestug.com@evil.example",
    ]);
  });
  it("fills samples for a preview and leaves unknown values alone", () => {
    expect(fillSample("{amount} on {date} for {fee}")).toBe("UGX 1,000,000 on 20 Nov 2026 for {fee}");
  });
});
