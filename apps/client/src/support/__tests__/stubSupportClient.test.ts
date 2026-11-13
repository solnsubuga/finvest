import { answers } from "../answers";
import { createStubSupportClient, DEMO_REPLY, replyDue } from "../stubSupportClient";
import { SupportError } from "../types";

function demo(options: { empty?: boolean } = {}) {
  const clock = { ms: Date.parse("2026-11-13T12:40:00Z") };
  const client = createStubSupportClient({ delayMs: 0, now: () => clock.ms, ...options });
  return { client, clock };
}

describe("notifications in the demo", () => {
  it("lists them newest first with the unread count", async () => {
    const { client } = demo();
    const page = await client.listNotifications();
    expect(page.items[0]?.title).toBe("Investment started");
    expect(page.unread).toBe(2);
    expect(page.nextBefore).toBeNull();
    const times = page.items.map((item) => Date.parse(item.createdAt));
    expect([...times].sort((a, b) => b - a)).toEqual(times);
  });

  it("marks one read, then all", async () => {
    const { client } = demo();
    const first = (await client.listNotifications()).items[0];
    expect(await client.markNotificationsRead([first?.id ?? ""])).toBe(1);
    expect(await client.markNotificationsRead()).toBe(0);
    expect((await client.listNotifications()).items.every((item) => item.read)).toBe(true);
  });

  it("never carries a web address, only a kind and an ID", async () => {
    const { client } = demo();
    const page = await client.listNotifications();
    for (const item of page.items) {
      expect(`${item.title} ${item.body}`).not.toMatch(/https?:|www\./);
      expect(Object.keys(item.link ?? { kind: "", id: "" }).sort()).toEqual(["id", "kind"]);
    }
  });

  it("shows no tax rate or percentage to the client", async () => {
    const { client } = demo();
    const page = await client.listNotifications();
    const everything = [
      ...page.items.map((item) => `${item.title} ${item.body}`),
      ...answers.flatMap((answer) => [answer.title, ...answer.paragraphs]),
    ].join(" ");
    expect(everything).not.toMatch(/%|percent|withholding/i);
  });
});

describe("alert choices in the demo", () => {
  it("starts from the defaults and saves a change", async () => {
    const { client } = demo();
    const before = await client.getPreferences();
    expect(before.find((row) => row.category === "money")?.channels).toEqual(["push", "sms"]);
    expect(before.find((row) => row.category === "earnings")?.channels).toEqual([]);
    const after = await client.savePreferences("earnings", ["email", "push"]);
    expect(after.find((row) => row.category === "earnings")?.channels).toEqual(["push", "email"]);
  });

  it("keeps SMS or email on for security alerts", async () => {
    const { client } = demo();
    await expect(client.savePreferences("security", ["push"])).rejects.toEqual(
      new SupportError("invalid"),
    );
    const kept = await client.savePreferences("security", ["email"]);
    expect(kept.find((row) => row.category === "security")?.channels).toEqual(["email"]);
  });
});

describe("the help chat in the demo", () => {
  it("is empty before the first message", async () => {
    const { client } = demo();
    expect(await client.getChat()).toEqual({ conversation: null, messages: [], unread: 0 });
  });

  it("posts a message once however often the same key is sent", async () => {
    const { client } = demo();
    const outgoing = { body: "  Hello  ", key: "key-00000001", topic: "other" as const };
    const first = await client.sendMessage(outgoing);
    const again = await client.sendMessage(outgoing);
    expect(again).toEqual(first);
    expect(first.body).toBe("Hello");
    expect((await client.getChat()).messages).toHaveLength(1);
  });

  it("refuses an empty message and one that is too long", async () => {
    const { client } = demo();
    for (const body of ["   ", "x".repeat(2001)]) {
      await expect(client.sendMessage({ body, key: `key-${body.length}-0000`, topic: "other" })).rejects.toEqual(
        new SupportError("invalid"),
      );
    }
  });

  it("replies a few seconds later, counts it unread and announces it", async () => {
    const { client, clock } = demo();
    const sent = await client.sendMessage({ body: "Hello", key: "key-00000001", topic: "other" });
    expect((await client.getChat(sent.id)).messages).toEqual([]);

    clock.ms += 5_000;
    const chat = await client.getChat(sent.id);
    expect(chat.messages.map((message) => message.body)).toEqual([DEMO_REPLY]);
    expect(chat.messages[0]?.author).toBe("staff");
    expect(chat.unread).toBe(1);
    const page = await client.listNotifications();
    expect(page.items[0]?.link?.kind).toBe("conversation");

    clock.ms += 1;
    await client.markChatRead();
    expect((await client.getChat()).unread).toBe(0);
  });
});

describe("complaints in the demo", () => {
  it("gives a new complaint a reference and a date to answer by", async () => {
    const { client } = demo({ empty: true });
    const complaint = await client.makeComplaint({
      category: "withdrawals",
      summary: " My withdrawal is late ",
      details: "Asked on Tuesday.",
    });
    expect(complaint.reference).toBe("C-1001");
    expect(complaint.summary).toBe("My withdrawal is late");
    expect(complaint.status).toBe("new");
    // Received on Friday 13 November, so five working days later is Friday 20 November.
    expect(complaint.dueOn).toBe("2026-11-20");
    expect(await client.getComplaint(complaint.id)).toEqual(complaint);
    expect((await client.listComplaints()).map((item) => item.reference)).toEqual(["C-1001"]);
    expect((await client.listNotifications()).items[0]?.link).toEqual({
      kind: "complaint",
      id: complaint.id,
    });
  });

  it("refuses a complaint with nothing in it, and an unknown one", async () => {
    const { client } = demo();
    await expect(
      client.makeComplaint({ category: "other", summary: " ", details: "x" }),
    ).rejects.toEqual(new SupportError("invalid"));
    await expect(client.getComplaint("nope")).rejects.toEqual(new SupportError("not_found"));
  });

  it("skips the weekend when it counts working days", () => {
    // Saturday 14 November: Monday is day one, so the fifth is Friday 20 November.
    expect(replyDue(Date.parse("2026-11-14T09:00:00Z"))).toBe("2026-11-20");
    // Monday 16 November: the fifth working day after it is Monday 23 November.
    expect(replyDue(Date.parse("2026-11-16T09:00:00Z"))).toBe("2026-11-23");
    // 22:30 UTC on Sunday is already Monday in Kampala.
    expect(replyDue(Date.parse("2026-11-15T22:30:00Z"))).toBe("2026-11-23");
  });
});
