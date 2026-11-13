import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import type { ClientCard, Directory } from "@/directory/api.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { emptyDirectory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import {
  notConnectedSupportApi,
  SupportError,
  supportErrorMessage,
  type Chat,
  type ConversationStatus,
  type InboxRow,
  type SavedReply,
  type SupportApi,
  type Topic,
} from "./api.ts";
import { AboutClient, ChatPanel, InboxItem, UNKNOWN_CLIENT } from "./Chat.tsx";
import { SavedReplies } from "./SavedReplies.tsx";
import { createStubSupportApi } from "./stub.ts";

const SUMMARY = "Answer clients with their account beside you.";

type Tab = ConversationStatus | "saved";
type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

interface SupportScreenProps {
  api: SupportApi;
  directory: Directory;
  /** The signed-in member of staff holds `support.reply`. The API checks it again. */
  canReply: boolean;
  /** Makes the idempotency key for one message. Every try at the same message carries the same key. */
  newKey?: () => string;
  now?: () => Date;
  /** How often the open tab asks for new messages. 0 turns it off, for tests. */
  pollMs?: number;
}

export function SupportScreen({
  api,
  directory,
  canReply,
  newKey = () => crypto.randomUUID(),
  now = () => new Date(),
  pollMs = 15_000,
}: SupportScreenProps) {
  const [tab, setTab] = useState<Tab>("open");
  const [inbox, setInbox] = useState<Load<InboxRow[]>>({ state: "loading" });
  const [openCount, setOpenCount] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [chat, setChat] = useState<Load<Chat> | null>(null);
  const [cards, setCards] = useState<Record<string, ClientCard | null>>({});
  const [saved, setSaved] = useState<Load<SavedReply[]>>({ state: "loading" });
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"reply" | "solve" | "topic" | null>(null);
  const [savedBusy, setSavedBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [clockNow, setClockNow] = useState(() => now());
  // The key of a message that has not been answered yet, by chat. A second press of Send
  // with the same words sends the same key, so a first try that did arrive is not posted twice.
  const pending = useRef(new Map<string, { body: string; key: string }>());
  const asked = useRef(new Set<string>());

  const lookUp = useCallback(
    (clientIds: readonly string[]) => {
      for (const clientId of clientIds) {
        if (asked.current.has(clientId)) continue;
        asked.current.add(clientId);
        void directory.client(clientId).then(
          (card) => setCards((known) => ({ ...known, [clientId]: card })),
          () => setCards((known) => ({ ...known, [clientId]: null })),
        );
      }
    },
    [directory],
  );

  const fetchInbox = useCallback(
    (status: ConversationStatus): Promise<Load<InboxRow[]>> =>
      api.inbox(status).then(
        (rows) => {
          lookUp(rows.map((row) => row.clientId));
          if (status === "open") setOpenCount(rows.length);
          return { state: "ready", value: rows };
        },
        (error: unknown) => ({ state: "failed", message: supportErrorMessage(error) }),
      ),
    [api, lookUp],
  );

  const openChat = useCallback(
    (conversationId: string, quiet = false) => {
      if (!quiet) setChat({ state: "loading" });
      return api.chat(conversationId).then(
        (value) => {
          lookUp([value.clientId]);
          setChat({ state: "ready", value });
          // Opening a chat marks it read, so its row stops counting new messages.
          setInbox((last) =>
            last.state === "ready"
              ? {
                  state: "ready",
                  value: last.value.map((row) => (row.conversation.id === conversationId ? { ...row, unread: 0 } : row)),
                }
              : last,
          );
        },
        (error: unknown) => {
          if (!quiet) setChat({ state: "failed", message: supportErrorMessage(error) });
        },
      );
    },
    [api, lookUp],
  );

  // The open and solved lists. The first chat in the list opens by itself, as on the canvas.
  useEffect(() => {
    if (tab === "saved") return;
    let current = true;
    void fetchInbox(tab).then((next) => {
      if (!current) return;
      setInbox(next);
      const first = next.state === "ready" ? next.value[0]?.conversation.id : undefined;
      setSelected(first ?? null);
      setChat(null);
      if (first) void openChat(first);
    });
    return () => {
      current = false;
    };
  }, [tab, fetchInbox, openChat]);

  // Saved replies are needed both in the chat and on their own tab.
  useEffect(() => {
    let current = true;
    void api.savedReplies().then(
      (value) => current && setSaved({ state: "ready", value }),
      (error: unknown) => current && setSaved({ state: "failed", message: supportErrorMessage(error) }),
    );
    return () => {
      current = false;
    };
  }, [api]);

  // New messages arrive by asking again, like the client app. Docs/support.md has why there is no socket.
  useEffect(() => {
    if (pollMs <= 0 || tab !== "open") return;
    const timer = setInterval(() => {
      setClockNow(now());
      void fetchInbox("open").then((next) => next.state === "ready" && setInbox(next));
      if (selected) void openChat(selected, true);
    }, pollMs);
    return () => clearInterval(timer);
  }, [pollMs, tab, selected, fetchInbox, openChat, now]);

  function select(conversationId: string) {
    setSelected(conversationId);
    setFailed(null);
    void openChat(conversationId);
  }

  function nameOf(clientId: string): string {
    return cards[clientId]?.name ?? UNKNOWN_CLIENT;
  }

  async function refreshInbox() {
    if (tab === "saved") return;
    const next = await fetchInbox(tab);
    if (next.state === "ready") setInbox(next);
  }

  async function send(conversationId: string) {
    const body = (drafts[conversationId] ?? "").trim();
    if (body.length === 0) return;
    const waiting = pending.current.get(conversationId);
    const key = waiting?.body === body ? waiting.key : newKey();
    pending.current.set(conversationId, { body, key });
    setBusy("reply");
    setFailed(null);
    try {
      await api.reply(conversationId, body, key);
      pending.current.delete(conversationId);
      setDrafts((all) => ({ ...all, [conversationId]: "" }));
      await Promise.all([openChat(conversationId, true), refreshInbox()]);
    } catch (error) {
      // Only an answer that never arrived leaves the message open. A refusal is an answer.
      if (error instanceof SupportError && error.code !== "unavailable") pending.current.delete(conversationId);
      setFailed({ on: conversationId, message: supportErrorMessage(error) });
      if (error instanceof SupportError && error.code === "conflict") await openChat(conversationId, true);
    } finally {
      setBusy(null);
    }
  }

  async function solve(conversationId: string) {
    setBusy("solve");
    setFailed(null);
    try {
      await api.solve(conversationId);
      const next = await fetchInbox("open");
      setInbox(next);
      const first = next.state === "ready" ? next.value[0]?.conversation.id : undefined;
      setSelected(first ?? null);
      if (first) await openChat(first);
      else setChat(null);
    } catch (error) {
      setFailed({ on: conversationId, message: supportErrorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function changeTopic(conversationId: string, topic: Topic) {
    setBusy("topic");
    setFailed(null);
    try {
      await api.setTopic(conversationId, topic);
      await Promise.all([openChat(conversationId, true), refreshInbox()]);
    } catch (error) {
      setFailed({ on: conversationId, message: supportErrorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function saveReply(title: string, body: string): Promise<boolean> {
    setSavedBusy("new");
    setFailed(null);
    try {
      const reply = await api.saveReply(title, body);
      setSaved((last) => (last.state === "ready" ? { state: "ready", value: [...last.value, reply] } : last));
      return true;
    } catch (error) {
      setFailed({ on: "saved", message: supportErrorMessage(error) });
      return false;
    } finally {
      setSavedBusy(null);
    }
  }

  async function retireReply(replyId: string) {
    setSavedBusy(replyId);
    setFailed(null);
    try {
      await api.retireReply(replyId);
      setSaved((last) =>
        last.state === "ready" ? { state: "ready", value: last.value.filter((reply) => reply.id !== replyId) } : last,
      );
    } catch (error) {
      setFailed({ on: "saved", message: supportErrorMessage(error) });
    } finally {
      setSavedBusy(null);
    }
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "open", label: openCount === null ? "Inbox" : `Inbox, ${openCount}` },
    { id: "solved", label: "Solved" },
    { id: "saved", label: "Saved replies" },
  ];
  const savedList = saved.state === "ready" ? saved.value : [];

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader title="Support and messages" summary={SUMMARY} />
        <div role="tablist" aria-label="Support" className="flex flex-wrap gap-2">
          {tabs.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => {
                if (id === tab) return;
                // The list for the new tab loads in the effect below.
                if (id !== "saved") setInbox({ state: "loading" });
                setTab(id);
                setFailed(null);
              }}
              className={cn(
                "h-11 cursor-pointer rounded-pill px-4 text-sm",
                tab === id ? "bg-black font-bold text-white" : "border border-border-strong bg-surface font-semibold text-ink",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {tab === "saved" ? (
        saved.state === "loading" ? (
          <p role="status" className="m-0 text-md text-muted">
            Loading saved replies
          </p>
        ) : saved.state === "failed" ? (
          <Failure title="Saved replies could not be loaded" message={saved.message} />
        ) : (
          <SavedReplies
            replies={saved.value}
            canReply={canReply}
            busy={savedBusy}
            error={failed?.on === "saved" ? failed.message : null}
            onSave={saveReply}
            onRetire={(replyId) => void retireReply(replyId)}
          />
        )
      ) : inbox.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading the inbox
        </p>
      ) : inbox.state === "failed" ? (
        <Failure
          title="The inbox could not be loaded"
          message={inbox.message}
          onRetry={() => {
            setInbox({ state: "loading" });
            void fetchInbox(tab).then(setInbox);
          }}
        />
      ) : inbox.value.length === 0 ? (
        <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-[17px] font-extrabold">{tab === "open" ? "No one is waiting" : "No solved chats yet"}</h2>
          <p className="m-0 text-md leading-normal text-muted">
            {tab === "open"
              ? "Every client has an answer. New messages show here as they come in."
              : "Chats show here once someone marks them as solved."}
          </p>
        </section>
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          <nav aria-label="Chats" className="flex min-w-0 flex-[1_1_240px] flex-col gap-2.5">
            {inbox.value.map((row) => (
              <InboxItem
                key={row.conversation.id}
                row={row}
                name={nameOf(row.clientId)}
                selected={row.conversation.id === selected}
                now={clockNow}
                onSelect={() => select(row.conversation.id)}
              />
            ))}
          </nav>
          {chat === null || chat.state === "loading" ? (
            <p role="status" className="m-0 flex-[2_1_360px] text-md text-muted">
              Opening the chat
            </p>
          ) : chat.state === "failed" ? (
            <div className="flex-[2_1_360px]">
              <Failure
                title="This chat could not be opened"
                message={chat.message}
                {...(selected ? { onRetry: () => void openChat(selected) } : {})}
              />
            </div>
          ) : (
            <ChatPanel
              chat={chat.value}
              name={nameOf(chat.value.clientId)}
              canReply={canReply}
              savedReplies={savedList}
              draft={drafts[chat.value.conversation.id] ?? ""}
              busy={busy}
              error={failed?.on === chat.value.conversation.id ? failed.message : null}
              onDraft={(text) => setDrafts((all) => ({ ...all, [chat.value.conversation.id]: text }))}
              onSend={() => void send(chat.value.conversation.id)}
              onSolve={() => void solve(chat.value.conversation.id)}
              onTopic={(topic) => void changeTopic(chat.value.conversation.id, topic)}
            />
          )}
          <aside aria-label="Client" className="flex min-w-0 flex-[1_1_260px] flex-col gap-4">
            {chat?.state === "ready" ? <AboutClient card={cards[chat.value.clientId]} /> : null}
          </aside>
        </div>
      )}
    </>
  );
}

function Failure({ title, message, onRetry }: { title: string; message: string; onRetry?: () => void }) {
  return (
    <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
      <h2 className="m-0 text-xl font-extrabold">{title}</h2>
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </section>
  );
}

export default function SupportPage() {
  const { session } = useAuth();
  // Sample chats under `pnpm dev` or a preview build, like sign-in. Otherwise nothing until the API client exists.
  const [{ api, directory }] = useState<{ api: SupportApi; directory: Directory }>(() => {
    if (isStubAuth() && session) {
      const me = { id: session.staffId, name: session.name };
      // Under the stub, `?sample=empty` shows the screen with nothing in it and `?sample=offline` as if Finvest could not be reached.
      const sample = new URLSearchParams(window.location.search).get("sample");
      if (sample === "offline") return { api: notConnectedSupportApi, directory: createStubDirectory(me) };
      return { api: createStubSupportApi(me, Date.now, sample === "empty" ? "empty" : "sample"), directory: createStubDirectory(me) };
    }
    return { api: notConnectedSupportApi, directory: emptyDirectory };
  });
  return <SupportScreen api={api} directory={directory} canReply={session ? can(session, "support.reply") : false} />;
}
