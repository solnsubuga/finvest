import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { createHttpSupportApi } from "./http.ts";

const CONVERSATION = {
  id: "-Nchat0000000000000A",
  topic: "withdrawal",
  status: "open",
  created_at: "2026-11-13T07:00:00Z",
  last_message_at: "2026-11-13T07:40:00Z",
  waiting_since: "2026-11-13T07:40:00Z",
};

const MESSAGE = {
  id: "-Nmessage00000000001",
  conversation_id: CONVERSATION.id,
  author: "staff",
  author_name: "Grace",
  body: "We are looking at it now.",
  created_at: "2026-11-13T07:45:00Z",
};

function reply(status: number, body?: unknown) {
  return Promise.resolve(
    status === 204
      ? new Response(null, { status })
      : new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
}

function refusal(status: number, code: string) {
  return reply(status, { request_id: "req", error: { code, message: code, fields: [] } });
}

describe("support over HTTP", () => {
  it("reads the inbox by status in the console's shape", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() =>
      reply(200, {
        conversations: [{ conversation: CONVERSATION, client_id: "-Nclient00000000000A", last_message: "Where is it?", last_author: "client", unread: 2 }],
      }),
    );
    const [row] = await createHttpSupportApi(createHttp("https://api.test", fetch)).inbox("solved");
    expect(fetch.mock.calls[0]?.[0]).toBe("https://api.test/staff/support/conversations?status=solved");
    expect(row).toEqual({
      conversation: {
        id: CONVERSATION.id,
        topic: "withdrawal",
        status: "open",
        createdAt: CONVERSATION.created_at,
        lastMessageAt: CONVERSATION.last_message_at,
        waitingSince: CONVERSATION.waiting_since,
      },
      clientId: "-Nclient00000000000A",
      lastMessage: "Where is it?",
      lastAuthor: "client",
      unread: 2,
    });
  });

  it("opens a chat, replies with the message's key, solves it and sets its topic", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, { conversation: CONVERSATION, client_id: "-Nclient00000000000A", messages: [MESSAGE] }))
      .mockImplementationOnce(() => reply(201, MESSAGE))
      .mockImplementationOnce(() => reply(200, { ...CONVERSATION, status: "solved", waiting_since: null }))
      .mockImplementationOnce(() => reply(200, { ...CONVERSATION, topic: "deposit" }));
    const api = createHttpSupportApi(createHttp("https://api.test", fetch));
    const chat = await api.chat(CONVERSATION.id);
    expect(chat.messages[0]).toMatchObject({ authorName: "Grace", conversationId: CONVERSATION.id });
    expect((await api.reply(CONVERSATION.id, "On it", "key-0001")).id).toBe(MESSAGE.id);
    expect((await api.solve(CONVERSATION.id)).status).toBe("solved");
    expect((await api.setTopic(CONVERSATION.id, "deposit")).topic).toBe("deposit");
    const calls = fetch.mock.calls.map((call) => [call[1]?.method, String(call[0]).replace("https://api.test/staff/support/conversations/", ""), call[1]?.body ?? null]);
    expect(calls).toEqual([
      ["GET", CONVERSATION.id, null],
      ["POST", `${CONVERSATION.id}/messages`, JSON.stringify({ body: "On it", key: "key-0001" })],
      ["POST", `${CONVERSATION.id}/solve`, null],
      ["PUT", `${CONVERSATION.id}/topic`, JSON.stringify({ topic: "deposit" })],
    ]);
  });

  it("lists, saves and retires saved replies", async () => {
    const saved = { id: "-Nsaved0000000000001", title: "Late withdrawal", body: "Withdrawals reach you within a day." };
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(() => reply(200, { replies: [saved] }))
      .mockImplementationOnce(() => reply(201, saved))
      .mockImplementationOnce(() => reply(204));
    const api = createHttpSupportApi(createHttp("https://api.test", fetch));
    expect(await api.savedReplies()).toEqual([saved]);
    expect(await api.saveReply(saved.title, saved.body)).toEqual(saved);
    await api.retireReply(saved.id);
    expect(fetch.mock.calls[2]?.[0]).toBe(`https://api.test/staff/support/saved-replies/${saved.id}`);
    expect(fetch.mock.calls[2]?.[1]?.method).toBe("DELETE");
  });

  it("turns refusals into the screen's errors", async () => {
    for (const [status, code, expected] of [
      [409, "conflict", "conflict"],
      [403, "forbidden", "forbidden"],
      [404, "not_found", "not_found"],
      [422, "validation_failed", "invalid"],
    ] as const) {
      const fetch = vi.fn<typeof globalThis.fetch>(() => refusal(status, code));
      await expect(createHttpSupportApi(createHttp("https://api.test", fetch)).reply("x", "y", "key-0001")).rejects.toMatchObject({ code: expected });
    }
    const offline = vi.fn<typeof globalThis.fetch>(() => Promise.reject(new TypeError("offline")));
    await expect(createHttpSupportApi(createHttp("https://api.test", offline)).inbox("open")).rejects.toMatchObject({ code: "unavailable" });
  });
});
