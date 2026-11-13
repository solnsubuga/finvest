import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { emptyDirectory } from "@/directory/api.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { notConnectedSupportApi, SupportError, type SupportApi } from "./api.ts";
import { AboutClient } from "./Chat.tsx";
import { SupportScreen } from "./index.tsx";
import { waited } from "./labels.ts";
import { createStubSupportApi } from "./stub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };
const START = Date.parse("2026-11-13T12:00:00Z");

function renderSupport({
  api = createStubSupportApi(ME, () => START),
  canReply = true,
  directory = createStubDirectory(ME),
}: { api?: SupportApi; canReply?: boolean; directory?: typeof emptyDirectory } = {}) {
  let keys = 0;
  render(
    <MemoryRouter>
      <SupportScreen
        api={api}
        directory={directory}
        canReply={canReply}
        newKey={() => `key-${++keys}`}
        now={() => new Date(START)}
        pollMs={0}
      />
    </MemoryRouter>,
  );
  return { api, user: userEvent.setup() };
}

const chats = () => screen.findByRole("navigation", { name: "Chats" });
const chatWith = (name: string) => screen.findByRole("region", { name: `Chat with ${name}` });

describe("support screen", () => {
  it("lists open chats with the longest wait first and opens the first", async () => {
    renderSupport();
    const list = await chats();
    const rows = within(list).getAllByRole("button");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Joan Namutebi1 h"),
      expect.stringContaining("David Mugisha31 min"),
      expect.stringContaining("Peter Okello12 min"),
      expect.stringContaining("Amara Nakato4 min"),
      expect.stringContaining("Sarah AchiengAnswered"),
    ]);
    expect(screen.getByRole("tab", { name: "Inbox, 5" })).toHaveAttribute("aria-selected", "true");
    // The open chat is read, the others still count their new message, and an answered one has none.
    await waitFor(() => expect(within(list).getByRole("button", { name: /Joan Namutebi/ })).not.toHaveTextContent("new"));
    expect(within(list).getByRole("button", { name: /David Mugisha/ })).toHaveTextContent("1 new");
    expect(within(list).getByRole("button", { name: /Sarah Achieng/ })).not.toHaveTextContent("new");
    const chat = await chatWith("Joan Namutebi");
    expect(within(chat).getByText("Can I change my payout bank account?")).toBeInTheDocument();
    const about = await screen.findByRole("region", { name: "About this client" });
    expect(await within(about).findByText("Account 1001003318, verified, client since 21 Jun 2026")).toBeInTheDocument();
    expect(within(about).getByRole("link", { name: "Open full client record" })).toHaveAttribute(
      "href",
      "/clients?client=sample-client-joan",
    );
  });

  it("replies with a saved reply, then moves the chat behind the ones still waiting", async () => {
    const { user } = renderSupport();
    await user.click(within(await chats()).getByRole("button", { name: /Amara Nakato/ }));
    const chat = await chatWith("Amara Nakato");
    await user.click(within(chat).getByRole("button", { name: "Payout is on its way" }));
    const box = within(chat).getByRole("textbox", { name: "Reply" });
    expect(box).toHaveValue(
      "Your payout has been sent again and is on its way. You will get an SMS from the network when it lands.",
    );
    await user.clear(box);
    await user.type(box, "Hello Amara, I am on it.");
    await user.click(within(chat).getByRole("button", { name: "Send" }));
    expect(await within(chat).findByText("Hello Amara, I am on it.")).toBeInTheDocument();
    expect(within(chat).getByText("Amina, 15:00")).toBeInTheDocument();
    expect(box).toHaveValue("");
    const amara = within(await chats()).getByRole("button", { name: /Amara Nakato/ });
    await waitFor(() => expect(amara).toHaveTextContent("You: Hello Amara, I am on it."));
    expect(amara).toHaveTextContent("Answered");
  });

  it("sends the same key again when a reply gets no answer, and a new one for new words", async () => {
    const stub = createStubSupportApi(ME, () => START);
    const reply = vi
      .fn<SupportApi["reply"]>()
      .mockRejectedValueOnce(new SupportError("unavailable"))
      .mockImplementation((id, body, key) => stub.reply(id, body, key));
    const { user } = renderSupport({ api: { ...stub, reply } });
    const chat = await chatWith("Joan Namutebi");
    const box = within(chat).getByRole("textbox", { name: "Reply" });
    await user.type(box, "Yes, from Profile.");
    await user.click(within(chat).getByRole("button", { name: "Send" }));
    expect(await within(chat).findByRole("alert")).toHaveTextContent("We could not reach Finvest.");
    expect(box).toHaveValue("Yes, from Profile.");
    await user.click(within(chat).getByRole("button", { name: "Send" }));
    await within(chat).findByText("Yes, from Profile.", { selector: "p" });
    await user.type(box, "Anything else?");
    await user.click(within(chat).getByRole("button", { name: "Send" }));
    await within(chat).findByText("Anything else?", { selector: "p" });
    expect(reply.mock.calls.map((call) => call[2])).toEqual(["key-1", "key-1", "key-2"]);
  });

  it("marks a chat as solved, opens the next and lists it under Solved", async () => {
    const { user } = renderSupport();
    const chat = await chatWith("Joan Namutebi");
    await user.click(within(chat).getByRole("button", { name: "Mark as solved" }));
    await chatWith("David Mugisha");
    expect(screen.getByRole("tab", { name: "Inbox, 4" })).toBeInTheDocument();
    expect(within(await chats()).queryByRole("button", { name: /Joan Namutebi/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Solved" }));
    const solved = await chatWith("Joan Namutebi");
    expect(within(solved).getByText(/Solved. The client's next message opens a new chat./)).toBeInTheDocument();
    expect(within(solved).queryByRole("form", { name: "Reply" })).not.toBeInTheDocument();
  });

  it("files a chat under another topic", async () => {
    const { user } = renderSupport();
    const chat = await chatWith("Joan Namutebi");
    await user.selectOptions(within(chat).getByRole("combobox", { name: "Topic" }), "withdrawal");
    const joan = within(await chats()).getByRole("button", { name: /Joan Namutebi/ });
    await waitFor(() => expect(joan).toHaveTextContent("Withdrawal"));
  });

  it("lets a role without support.reply read chats but not answer them", async () => {
    renderSupport({ canReply: false });
    const chat = await chatWith("Joan Namutebi");
    expect(within(chat).getByText("Your role can read chats but not reply to them.")).toBeInTheDocument();
    expect(within(chat).queryByRole("textbox")).not.toBeInTheDocument();
    expect(within(chat).queryByRole("button", { name: "Mark as solved" })).not.toBeInTheDocument();
    expect(within(chat).getByRole("combobox", { name: "Topic" })).toBeDisabled();
  });

  it("names a client it cannot look up only as a client", async () => {
    renderSupport({ directory: emptyDirectory });
    const chat = await chatWith("Client");
    expect(chat).toBeInTheDocument();
    const about = await screen.findByRole("region", { name: "About this client" });
    expect(await within(about).findByText(/Their details are not available here yet/)).toBeInTheDocument();
  });

  it("keeps saved replies: adds one and retires one", async () => {
    const { user } = renderSupport();
    await user.click(await screen.findByRole("tab", { name: "Saved replies" }));
    const form = await screen.findByRole("form", { name: "Save a reply" });
    await user.type(within(form).getByLabelText("Name on the button"), "Selfie tips");
    await user.type(within(form).getByLabelText("What it says"), "Face the light and take off your glasses.");
    await user.click(within(form).getByRole("button", { name: "Save reply" }));
    expect(await screen.findByRole("article", { name: "Selfie tips" })).toBeInTheDocument();
    expect(within(form).getByLabelText("Name on the button")).toHaveValue("");
    await user.click(within(screen.getByRole("article", { name: "Payout is on its way" })).getByRole("button", { name: "Retire" }));
    await waitFor(() => expect(screen.queryByRole("article", { name: "Payout is on its way" })).not.toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Inbox, 5" }));
    const chat = await chatWith("Joan Namutebi");
    expect(within(chat).getByRole("button", { name: "Selfie tips" })).toBeInTheDocument();
    expect(within(chat).queryByRole("button", { name: "Payout is on its way" })).not.toBeInTheDocument();
  });

  it("shows an empty inbox", async () => {
    const stub = createStubSupportApi(ME, () => START);
    renderSupport({ api: { ...stub, inbox: () => Promise.resolve([]) } });
    expect(await screen.findByRole("heading", { name: "No one is waiting" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Inbox, 0" })).toBeInTheDocument();
  });

  it("says when support is not connected, and can try again", async () => {
    const { user } = renderSupport({ api: notConnectedSupportApi });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The inbox could not be loaded");
    expect(alert).toHaveTextContent("Support is not connected to the Finvest service yet.");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected");
  });
});

describe("about this client", () => {
  const card = { clientId: "c1", name: "Ivan Ssemanda", accountNumber: "1001002291", verified: true, clientSince: "2026-03-30" };

  it("warns that a locked client cannot read a reply", () => {
    render(
      <MemoryRouter>
        <AboutClient card={{ ...card, status: "locked" }} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/Their account is locked. They cannot sign in to read a reply/)).toBeInTheDocument();
  });

  it("says nothing extra for an active client", () => {
    render(
      <MemoryRouter>
        <AboutClient card={{ ...card, status: "active" }} />
      </MemoryRouter>,
    );
    expect(screen.queryByText(/Their account is/)).not.toBeInTheDocument();
  });
});

describe("waited", () => {
  const now = new Date("2026-11-13T12:00:00Z");
  it.each([
    ["2026-11-13T12:00:30Z", "0 min"],
    ["2026-11-13T11:56:00Z", "4 min"],
    ["2026-11-13T11:00:00Z", "1 h"],
    ["2026-11-12T11:00:00Z", "1 day"],
    ["2026-11-10T12:00:00Z", "3 days"],
    ["2026-11-13T12:05:00Z", "0 min"],
  ])("%s is %s", (since, label) => {
    expect(waited(since, now)).toBe(label);
  });
});
