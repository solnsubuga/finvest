import { SupportError, type Chat, type Conversation, type InboxRow, type Message, type SavedReply, type SupportApi } from "./api.ts";

/**
 * A sample inbox for `pnpm dev` and previews, with the chats drawn on the
 * canvas. Not real clients and not connected to Finvest. Replies change only
 * this browser tab's copy.
 */

const MINUTE = 60_000;

interface Stored {
  conversation: Conversation;
  clientId: string;
  messages: Message[];
  staffReadAt: number;
}

export function createStubSupportApi(
  me: { id: string; name: string },
  now: () => number = Date.now,
  seed: "sample" | "empty" = "sample",
): SupportApi {
  const start = now();
  const at = (minutesAgo: number) => new Date(start - minutesAgo * MINUTE).toISOString();
  let counter = 0;
  const id = (prefix: string) => `${prefix}-${++counter}`;
  const firstName = me.name.split(/\s+/)[0] ?? me.name;
  const byKey = new Map<string, Message>();

  function chat(clientId: string, topic: Conversation["topic"], lines: [Message["author"], string, number][]): Stored {
    const conversationId = id("sample-chat");
    const messages = lines.map(([author, body, ago]) => ({
      id: id("sample-message"),
      conversationId,
      author,
      authorName: author === "staff" ? "Grace" : null,
      body,
      createdAt: at(ago),
    }));
    const last = messages[messages.length - 1] as Message;
    const waitingFrom = [...messages].reverse().findIndex((message) => message.author === "staff");
    const clientRun = waitingFrom === -1 ? messages : messages.slice(messages.length - waitingFrom);
    return {
      conversation: {
        id: conversationId,
        topic,
        status: "open",
        createdAt: (messages[0] as Message).createdAt,
        lastMessageAt: last.createdAt,
        waitingSince: last.author === "client" ? (clientRun[0] as Message).createdAt : null,
      },
      clientId,
      messages,
      staffReadAt: 0,
    };
  }

  const chats: Stored[] = seed === "empty" ? [] : [
    chat("sample-client-amara", "withdrawal", [["client", "Hi, my withdrawal of 150,000 from Tuesday still says processing.", 4]]),
    chat("sample-client-peter", "deposit", [["client", "I sent 2m by bank this morning, not showing.", 12]]),
    chat("sample-client-david", "identity", [["client", "Why was my selfie not accepted?", 31]]),
    chat("sample-client-joan", "account", [["client", "Can I change my payout bank account?", 60]]),
    chat("sample-client-sarah", "investment", [
      ["client", "When does my 91 day bill mature?", 300],
      ["staff", "Hello Sarah, it matures on Wednesday 18 November and is paid to your MTN number.", 290],
    ]),
  ];
  const saved: SavedReply[] = seed === "empty" ? [] : [
    {
      id: "sample-reply-1",
      title: "Payout is on its way",
      body: "Your payout has been sent again and is on its way. You will get an SMS from the network when it lands.",
    },
    {
      id: "sample-reply-2",
      title: "Share payment status",
      body: "Here is where your payment is right now. It updates in the app as soon as the network answers.",
    },
  ];

  const find = (conversationId: string): Stored => {
    const found = chats.find((stored) => stored.conversation.id === conversationId);
    if (!found) throw new SupportError("not_found");
    return found;
  };
  const copy = (stored: Stored): Chat => ({
    conversation: { ...stored.conversation },
    clientId: stored.clientId,
    messages: stored.messages.map((message) => ({ ...message })),
  });

  return {
    inbox(status) {
      const rows: InboxRow[] = chats
        .filter((stored) => stored.conversation.status === status)
        .map((stored) => {
          const last = stored.messages[stored.messages.length - 1] as Message;
          return {
            conversation: { ...stored.conversation },
            clientId: stored.clientId,
            lastMessage: last.body,
            lastAuthor: last.author,
            // Client messages staff have neither opened nor answered.
            unread: stored.messages.filter((message, index) => {
              const answered = stored.messages.slice(index + 1).some((later) => later.author === "staff");
              return message.author === "client" && !answered && Date.parse(message.createdAt) > stored.staffReadAt;
            }).length,
          };
        });
      // Waiting longest first, then the most recent.
      rows.sort((a, b) => {
        const wa = a.conversation.waitingSince;
        const wb = b.conversation.waitingSince;
        if (wa && wb) return wa.localeCompare(wb);
        if (wa) return -1;
        if (wb) return 1;
        return b.conversation.lastMessageAt.localeCompare(a.conversation.lastMessageAt);
      });
      return Promise.resolve(rows);
    },
    chat(conversationId) {
      try {
        const stored = find(conversationId);
        stored.staffReadAt = now();
        return Promise.resolve(copy(stored));
      } catch (error) {
        return Promise.reject(error);
      }
    },
    reply(conversationId, body, key) {
      const known = byKey.get(key);
      if (known) return Promise.resolve({ ...known });
      const text = body.trim();
      if (text.length === 0 || text.length > 2000) return Promise.reject(new SupportError("invalid"));
      let stored: Stored;
      try {
        stored = find(conversationId);
      } catch (error) {
        return Promise.reject(error);
      }
      if (stored.conversation.status === "solved") return Promise.reject(new SupportError("conflict"));
      const message: Message = {
        id: id("sample-message"),
        conversationId,
        author: "staff",
        authorName: firstName,
        body: text,
        createdAt: new Date(now()).toISOString(),
      };
      stored.messages.push(message);
      stored.conversation.lastMessageAt = message.createdAt;
      stored.conversation.waitingSince = null;
      byKey.set(key, message);
      return Promise.resolve({ ...message });
    },
    solve(conversationId) {
      try {
        const stored = find(conversationId);
        if (stored.conversation.status === "solved") throw new SupportError("conflict");
        stored.conversation.status = "solved";
        stored.conversation.waitingSince = null;
        return Promise.resolve({ ...stored.conversation });
      } catch (error) {
        return Promise.reject(error);
      }
    },
    setTopic(conversationId, topic) {
      try {
        const stored = find(conversationId);
        stored.conversation.topic = topic;
        return Promise.resolve({ ...stored.conversation });
      } catch (error) {
        return Promise.reject(error);
      }
    },
    savedReplies: () => Promise.resolve(saved.map((reply) => ({ ...reply }))),
    saveReply(title, body) {
      if (title.trim().length === 0 || body.trim().length === 0 || title.trim().length > 60) {
        return Promise.reject(new SupportError("invalid"));
      }
      const reply = { id: id("sample-reply"), title: title.trim(), body: body.trim() };
      saved.push(reply);
      return Promise.resolve({ ...reply });
    },
    retireReply(replyId) {
      const index = saved.findIndex((reply) => reply.id === replyId);
      if (index === -1) return Promise.reject(new SupportError("not_found"));
      saved.splice(index, 1);
      return Promise.resolve();
    },
  };
}
