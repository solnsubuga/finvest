import { API_URL } from "@/api/config";
import { setFetch } from "@/api/http";

import { createApiSupportClient } from "../apiSupportClient";
import { SupportError } from "../types";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

const notification = {
  id: "NNNNNNNNNNNNNNNNNNNN",
  category: "money",
  event: "deposit.confirmed",
  title: "Payment received",
  body: "We received UGX 300,000.",
  link: { kind: "deposit", id: "DDDDDDDDDDDDDDDDDDDD" },
  created_at: "2026-11-13T10:00:00Z",
  read: false,
};

const message = {
  id: "MMMMMMMMMMMMMMMMMMMM",
  conversation_id: "CCCCCCCCCCCCCCCCCCCC",
  author: "client",
  author_name: null,
  body: "Hello",
  created_at: "2026-11-13T10:00:00Z",
};

const complaint = {
  id: "KKKKKKKKKKKKKKKKKKKK",
  reference: "CMP-000001",
  category: "payments",
  summary: "Late payment",
  details: "My payment has not shown.",
  status: "new",
  received_at: "2026-11-13T10:00:00Z",
  due_on: "2026-11-27",
  closed_at: null,
  outcome: null,
  resolution: null,
};

type Seen = { method: string; path: string; body: unknown };

function serve(...answers: [number, unknown][]): Seen[] {
  const seen: Seen[] = [];
  setFetch(async (url, init) => {
    seen.push({
      method: String(init?.method),
      path: String(url).replace(API_URL, ""),
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    const [status, body] = answers[Math.min(seen.length, answers.length) - 1]!;
    return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(body), { status });
  });
  return seen;
}

function refusal(status: number, code: string) {
  return { request_id: "R".repeat(20), error: { code, message: "x", fields: [] } };
}

afterEach(() => setFetch(null));

describe("the support client for the real API", () => {
  it("pages notifications and keeps the link a screen opens", async () => {
    const seen = serve([200, { items: [notification], unread: 1, next_before: "cursor-1" }]);
    const page = await createApiSupportClient().listNotifications("cursor-0");
    expect(seen[0]).toMatchObject({ method: "GET", path: "/client/notifications?before=cursor-0" });
    expect(page).toEqual({
      items: [
        {
          id: notification.id,
          category: "money",
          title: "Payment received",
          body: "We received UGX 300,000.",
          link: { kind: "deposit", id: "DDDDDDDDDDDDDDDDDDDD" },
          createdAt: "2026-11-13T10:00:00Z",
          read: false,
        },
      ],
      unread: 1,
      nextBefore: "cursor-1",
    });
  });

  it("asks for the first page without a cursor", async () => {
    const seen = serve([200, { items: [], unread: 0, next_before: null }]);
    await createApiSupportClient().listNotifications();
    expect(seen[0]!.path).toBe("/client/notifications");
  });

  it("marks chosen notifications read, or all of them", async () => {
    const seen = serve([200, { unread: 0 }]);
    const client = createApiSupportClient();
    await expect(client.markNotificationsRead([notification.id])).resolves.toBe(0);
    await client.markNotificationsRead();
    expect(seen.map((s) => s.body)).toEqual([{ ids: [notification.id] }, { ids: null }]);
  });

  it("saves one category's channels and reads back every category", async () => {
    const categories = [
      { category: "money", channels: ["push", "sms"], always_on: false },
      { category: "security", channels: ["push", "sms", "email"], always_on: true },
    ];
    const seen = serve([200, { categories }]);
    const saved = await createApiSupportClient().savePreferences("money", ["push", "sms"]);
    expect(seen[0]).toMatchObject({
      method: "PUT",
      path: "/client/notification-preferences",
      body: { categories: [{ category: "money", channels: ["push", "sms"] }] },
    });
    expect(saved[1]).toEqual({ category: "security", channels: ["push", "sms", "email"], alwaysOn: true });
  });

  it("reads the chat after the last message it has", async () => {
    const conversation = {
      id: message.conversation_id,
      status: "open",
      topic: "deposit",
      created_at: "2026-11-13T10:00:00Z",
      last_message_at: "2026-11-13T10:00:00Z",
      waiting_since: null,
    };
    const seen = serve([200, { conversation, messages: [message], unread: 0 }]);
    const chat = await createApiSupportClient().getChat(message.id);
    expect(seen[0]!.path).toBe(`/client/support/chat?after=${message.id}`);
    expect(chat).toEqual({
      conversation: { id: message.conversation_id, status: "open" },
      messages: [
        {
          id: message.id,
          conversationId: message.conversation_id,
          author: "client",
          authorName: null,
          body: "Hello",
          createdAt: "2026-11-13T10:00:00Z",
        },
      ],
      unread: 0,
    });
  });

  it("sends a message under the screen's key, so a retry is sent once", async () => {
    const seen = serve([201, message]);
    await createApiSupportClient().sendMessage({ body: "Hello", key: "key-00000001", topic: "deposit" });
    expect(seen[0]).toMatchObject({
      method: "POST",
      path: "/client/support/messages",
      body: { body: "Hello", key: "key-00000001", topic: "deposit" },
    });
  });

  it("makes a complaint and reads one by its id", async () => {
    const seen = serve([201, complaint], [200, complaint]);
    const client = createApiSupportClient();
    const made = await client.makeComplaint({ category: "payments", summary: "Late payment", details: "x" });
    expect(made).toMatchObject({ reference: "CMP-000001", dueOn: "2026-11-27", outcome: null });
    await client.getComplaint(complaint.id);
    expect(seen[1]).toMatchObject({ method: "GET", path: `/client/complaints/${complaint.id}` });
  });

  it("turns refusals and lost connections into reasons the screens show", async () => {
    const client = createApiSupportClient();
    serve([404, refusal(404, "not_found")]);
    await expect(client.getComplaint("X".repeat(20))).rejects.toEqual(new SupportError("not_found"));
    serve([422, refusal(422, "validation_failed")]);
    await expect(client.makeComplaint({ category: "other", summary: "", details: "" })).rejects.toEqual(
      new SupportError("invalid"),
    );
    serve([500, refusal(500, "internal_error")]);
    await expect(client.listComplaints()).rejects.toEqual(new SupportError("unavailable"));
    setFetch(async () => {
      throw new TypeError("Network request failed");
    });
    await expect(client.getChat()).rejects.toEqual(new SupportError("unavailable"));
  });

  it("refuses an answer that is not what the contract says", async () => {
    serve([200, { items: [{ id: 1 }], unread: 0, next_before: null }]);
    await expect(createApiSupportClient().listNotifications()).rejects.toEqual(new SupportError("unavailable"));
  });
});
