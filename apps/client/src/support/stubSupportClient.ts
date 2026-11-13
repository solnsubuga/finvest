import {
  Category,
  CategoryChannels,
  Channel,
  Chat,
  Complaint,
  MAX_DETAILS,
  MAX_MESSAGE,
  MAX_SUMMARY,
  Message,
  Notification,
  SupportClient,
  SupportError,
} from "./types";

/**
 * An in-memory stand-in for the notification, help chat and complaint routes,
 * so the screens can be built and tested before the backend routes are live.
 * Nothing here is real: no network, no member of staff, and everything is
 * forgotten when the app reloads. `resolveSupport.ts` keeps it out of
 * production builds.
 *
 * The notifications are the ones on the design canvas. A message sent to the
 * demo chat gets one canned reply a few seconds later, so the unread dot and
 * the polling can be seen.
 */
/** The demo sign-in's two ready-made accounts. They start with notifications and one old complaint. */
export const SEEDED_ACCOUNTS = ["+256772123456", "+256701000200"];
export const DEMO_STAFF_NAME = "Grace";
export const DEMO_REPLY =
  "Thanks for writing. This is the demo, so no one at Finvest has seen your message.";

/** How long the demo's reply takes. */
const REPLY_MS = 4_000;
const PAGE_SIZE = 20;
const WORKING_DAYS_TO_REPLY = 5;
const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

const DEFAULTS: Record<Category, Channel[]> = {
  money: ["push", "sms"],
  investments: ["push"],
  earnings: [],
  account: ["push", "email"],
  security: ["push", "sms", "email"],
  support: ["push"],
  news: ["push"],
};
const ALWAYS_ON: Category[] = ["security"];
const CATEGORIES = Object.keys(DEFAULTS) as Category[];
const CHANNELS: Channel[] = ["push", "sms", "email"];

type Seed = Omit<Notification, "id" | "createdAt"> & { agoMs: number };

const SEEDS: Seed[] = [
  {
    category: "investments",
    title: "Investment started",
    body: "UGX 1,000,000 from MTN Mobile Money is now in a 91 Day Treasury Bill.",
    link: { kind: "investment", id: "demo-investment" },
    read: false,
    agoMs: 1 * HOUR,
  },
  {
    category: "earnings",
    title: "You earned UGX 2,416 today",
    body: "Interest across 3 investments, after tax.",
    link: null,
    read: false,
    agoMs: 10 * HOUR,
  },
  {
    category: "money",
    title: "Withdrawal is being processed",
    body: "UGX 150,000 to MTN Mobile Money. We will confirm when it is sent.",
    link: { kind: "payout", id: "demo-payout" },
    read: true,
    agoMs: 3 * DAY,
  },
  {
    category: "account",
    title: "October statement is ready",
    body: "Open it in Documents or have it emailed to you.",
    link: { kind: "statement", id: "demo-statement" },
    read: true,
    agoMs: 12 * DAY,
  },
  {
    category: "security",
    title: "New sign-in to your account",
    body: "Your account was signed in on a new phone. If this was not you, change your password now.",
    link: { kind: "security", id: "demo-sign-in" },
    read: true,
    agoMs: 20 * DAY,
  },
  {
    category: "account",
    title: "Identity check approved",
    body: "Your account is fully open.",
    link: null,
    read: true,
    agoMs: 34 * DAY,
  },
];

export type StubOptions = {
  /** Artificial delay so loading states are visible. Tests pass 0. */
  delayMs?: number;
  /** The clock, in milliseconds. Tests move it by hand. */
  now?: () => number;
  /** Start with nothing, to see the empty states. */
  empty?: boolean;
};

/** The reply-by date: so many working days after the day received, Monday to Friday. */
export function replyDue(receivedMs: number): string {
  // Kampala is three hours ahead of UTC all year.
  const day = new Date(receivedMs + 3 * HOUR);
  let left = WORKING_DAYS_TO_REPLY;
  while (left > 0) {
    day.setUTCDate(day.getUTCDate() + 1);
    const weekday = day.getUTCDay();
    if (weekday !== 0 && weekday !== 6) {
      left -= 1;
    }
  }
  return day.toISOString().slice(0, 10);
}

function clean(text: string, limit: number): string {
  const trimmed = text.trim();
  if (trimmed.length === 0 || trimmed.length > limit) {
    throw new SupportError("invalid");
  }
  return trimmed;
}

export function createStubSupportClient(options: StubOptions = {}): SupportClient {
  const delayMs = options.delayMs ?? 350;
  const now = options.now ?? Date.now;
  const startedAt = now();
  let counter = 0;
  const nextId = (prefix: string) => {
    counter += 1;
    return `${prefix}${String(counter).padStart(4, "0")}`;
  };

  // Newest first, as the route returns them.
  const notifications: Notification[] = options.empty
    ? []
    : SEEDS.map(({ agoMs, ...seed }) => ({
        ...seed,
        id: nextId("n"),
        createdAt: new Date(startedAt - agoMs).toISOString(),
      }));
  const preferences = new Map<Category, Channel[]>(
    CATEGORIES.map((category) => [category, [...DEFAULTS[category]]]),
  );
  const messages: Message[] = [];
  const byKey = new Map<string, Message>();
  /** Replies that are on their way: shown once the clock passes `at`. */
  const pending: { at: number; message: Message }[] = [];
  let conversationId: string | null = null;
  let readThrough = 0;
  const complaints: Complaint[] = options.empty
    ? []
    : [
        {
          id: nextId("k"),
          reference: "C-1000",
          category: "payments",
          summary: "My payment took two days to show",
          details: "I paid by bank transfer on Monday and the investment only started on Wednesday.",
          status: "closed",
          receivedAt: new Date(startedAt - 40 * DAY).toISOString(),
          dueOn: replyDue(startedAt - 40 * DAY),
          closedAt: new Date(startedAt - 37 * DAY).toISOString(),
          outcome: "upheld",
          resolution:
            "The transfer reached us on Monday but was matched late. We have started your investment from Monday, so you lost no interest.",
        },
      ];
  let complaintNumber = 1000;

  const pause = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();

  function unreadNotifications(): number {
    return notifications.filter((notification) => !notification.read).length;
  }

  function listPreferences(): CategoryChannels[] {
    return CATEGORIES.map((category) => ({
      category,
      channels: CHANNELS.filter((channel) => preferences.get(category)?.includes(channel)),
      alwaysOn: ALWAYS_ON.includes(category),
    }));
  }

  /** Moves replies whose time has come into the chat, and announces each one. */
  function deliver() {
    while (pending.length > 0 && (pending[0] as { at: number }).at <= now()) {
      const { message } = pending.shift() as { message: Message };
      messages.push(message);
      notifications.unshift({
        id: nextId("n"),
        category: "support",
        title: "You have a reply from Finvest",
        body: "Open Help to read it.",
        link: { kind: "conversation", id: message.conversationId },
        createdAt: message.createdAt,
        read: false,
      });
    }
  }

  function unreadReplies(): number {
    return messages.filter(
      (message) => message.author === "staff" && Date.parse(message.createdAt) > readThrough,
    ).length;
  }

  return {
    async listNotifications(before) {
      await pause();
      deliver();
      const from = before
        ? notifications.findIndex((notification) => notification.id === before) + 1
        : 0;
      const items = notifications.slice(from, from + PAGE_SIZE);
      const more = from + PAGE_SIZE < notifications.length;
      return {
        items: items.map((item) => ({ ...item })),
        unread: unreadNotifications(),
        nextBefore: more ? ((items[items.length - 1] as Notification).id ?? null) : null,
      };
    },

    async markNotificationsRead(ids) {
      await pause();
      for (const notification of notifications) {
        if (!ids || ids.includes(notification.id)) {
          notification.read = true;
        }
      }
      return unreadNotifications();
    },

    async getPreferences() {
      await pause();
      return listPreferences();
    },

    async savePreferences(category, channels) {
      await pause();
      const wanted = CHANNELS.filter((channel) => channels.includes(channel));
      // As the backend refuses it: a security alert always keeps SMS or email.
      if (ALWAYS_ON.includes(category) && !wanted.includes("sms") && !wanted.includes("email")) {
        throw new SupportError("invalid");
      }
      preferences.set(category, wanted);
      return listPreferences();
    },

    async getChat(after) {
      await pause();
      deliver();
      const from = after ? messages.findIndex((message) => message.id === after) + 1 : 0;
      const chat: Chat = {
        conversation: conversationId ? { id: conversationId, status: "open" } : null,
        messages: messages.slice(from).map((message) => ({ ...message })),
        unread: unreadReplies(),
      };
      return chat;
    },

    async sendMessage(outgoing) {
      await pause();
      const known = byKey.get(outgoing.key);
      if (known) {
        return { ...known };
      }
      const body = clean(outgoing.body, MAX_MESSAGE);
      conversationId ??= nextId("c");
      const message: Message = {
        id: nextId("m"),
        conversationId,
        author: "client",
        authorName: null,
        body,
        createdAt: new Date(now()).toISOString(),
      };
      messages.push(message);
      byKey.set(outgoing.key, message);
      // One canned reply for the first message of each quiet spell, not one per message.
      if (pending.length === 0) {
        const at = now() + REPLY_MS;
        pending.push({
          at,
          message: {
            id: nextId("m"),
            conversationId,
            author: "staff",
            authorName: DEMO_STAFF_NAME,
            body: DEMO_REPLY,
            createdAt: new Date(at).toISOString(),
          },
        });
      }
      return { ...message };
    },

    async markChatRead() {
      await pause();
      readThrough = now();
    },

    async listComplaints() {
      await pause();
      return complaints.map((complaint) => ({ ...complaint }));
    },

    async getComplaint(complaintId) {
      await pause();
      const complaint = complaints.find((candidate) => candidate.id === complaintId);
      if (!complaint) {
        throw new SupportError("not_found");
      }
      return { ...complaint };
    },

    async makeComplaint(input) {
      await pause();
      const summary = clean(input.summary, MAX_SUMMARY);
      const details = clean(input.details, MAX_DETAILS);
      complaintNumber += 1;
      const complaint: Complaint = {
        id: nextId("k"),
        reference: `C-${complaintNumber}`,
        category: input.category,
        summary,
        details,
        status: "new",
        receivedAt: new Date(now()).toISOString(),
        dueOn: replyDue(now()),
        closedAt: null,
        outcome: null,
        resolution: null,
      };
      complaints.unshift(complaint);
      notifications.unshift({
        id: nextId("n"),
        category: "support",
        title: `We have your complaint ${complaint.reference}`,
        body: "We will answer within 5 working days. You can follow it in Help.",
        link: { kind: "complaint", id: complaint.id },
        createdAt: complaint.receivedAt,
        read: false,
      });
      return { ...complaint };
    },
  };
}
