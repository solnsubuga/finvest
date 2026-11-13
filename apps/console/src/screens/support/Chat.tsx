import { Link } from "react-router";
import type { ClientCard } from "@/directory/api.ts";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { MAX_MESSAGE, type Chat, type InboxRow, type SavedReply, type Topic } from "./api.ts";
import { clock, longDate, TOPIC_LABELS, TOPICS, waited } from "./labels.ts";

export const UNKNOWN_CLIENT = "Client";

/** One row of the inbox list: who, how long they have waited, and what they last wrote. */
export function InboxItem({
  row,
  name,
  selected,
  now,
  onSelect,
}: {
  row: InboxRow;
  name: string;
  selected: boolean;
  now: Date;
  onSelect: () => void;
}) {
  const { conversation } = row;
  const waiting = conversation.waitingSince;
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        "flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-[16px] bg-surface px-4 py-3.5 text-left text-ink",
        selected ? "border-2 border-black px-[15px] py-[13px]" : "border border-border",
      )}
    >
      <span className="flex w-full justify-between gap-2 text-md font-bold">
        <span className="min-w-0 truncate">{name}</span>
        <span className="shrink-0 text-xs font-semibold text-muted">
          {waiting ? waited(waiting, now) : conversation.status === "solved" ? "Solved" : "Answered"}
        </span>
      </span>
      <span className="line-clamp-2 text-sm text-muted">
        {row.lastAuthor === "staff" ? "You: " : ""}
        {row.lastMessage}
      </span>
      <span className="mt-1.5 flex items-center gap-2">
        <span className="rounded-pill bg-surface-sunken px-2 py-0.5 text-2xs font-bold text-black">
          {TOPIC_LABELS[conversation.topic]}
        </span>
        {row.unread > 0 ? (
          <span className="rounded-pill bg-lime px-2 py-0.5 text-2xs font-bold text-ink">
            {row.unread === 1 ? "1 new" : `${row.unread} new`}
          </span>
        ) : null}
      </span>
    </button>
  );
}

interface ChatPanelProps {
  chat: Chat;
  name: string;
  canReply: boolean;
  savedReplies: readonly SavedReply[];
  draft: string;
  busy: "reply" | "solve" | "topic" | null;
  error: string | null;
  onDraft: (text: string) => void;
  onSend: () => void;
  onSolve: () => void;
  onTopic: (topic: Topic) => void;
}

/** The open chat, with saved replies above the reply box as on the canvas. */
export function ChatPanel({
  chat,
  name,
  canReply,
  savedReplies,
  draft,
  busy,
  error,
  onDraft,
  onSend,
  onSolve,
  onTopic,
}: ChatPanelProps) {
  const { conversation } = chat;
  const open = conversation.status === "open";
  const tooLong = draft.length > MAX_MESSAGE;
  return (
    <section
      aria-label={`Chat with ${name}`}
      className="flex min-w-0 flex-[2_1_360px] flex-col gap-3 rounded-[20px] border border-border bg-surface p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="m-0 text-[18px] font-extrabold">{name}</h2>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-xs font-bold text-muted">
            Topic
            <select
              value={conversation.topic}
              disabled={!canReply || busy !== null}
              onChange={(event) => onTopic(event.target.value as Topic)}
              className="h-11 rounded-pill border border-border-strong bg-surface px-3 text-sm font-semibold text-ink"
            >
              {TOPICS.map((topic) => (
                <option key={topic} value={topic}>
                  {TOPIC_LABELS[topic]}
                </option>
              ))}
            </select>
          </label>
          {open && canReply ? (
            <Button variant="secondary" className="px-3.5" disabled={busy !== null} onClick={onSolve}>
              {busy === "solve" ? "Marking as solved" : "Mark as solved"}
            </Button>
          ) : null}
        </div>
      </div>
      <ol aria-label="Messages" className="m-0 flex list-none flex-col gap-3 p-0">
        {chat.messages.map((message) => {
          const mine = message.author === "staff";
          return (
            <li key={message.id} className={cn("flex max-w-[80%] flex-col gap-1", mine ? "self-end items-end" : "self-start")}>
              <p
                className={cn(
                  "m-0 whitespace-pre-wrap px-3.5 py-2.5 text-md leading-[1.45]",
                  mine ? "rounded-[16px_16px_4px_16px] bg-black text-white" : "rounded-[16px_16px_16px_4px] bg-paper",
                )}
              >
                {message.body}
              </p>
              <span className="text-xs text-muted">
                {mine ? `${message.authorName ?? "Finvest"}, ` : ""}
                {clock(message.createdAt)}
              </span>
            </li>
          );
        })}
      </ol>
      {open ? null : (
        <p className="m-0 self-center text-xs text-muted">
          Solved. The client&apos;s next message opens a new chat.
        </p>
      )}
      <div className="min-h-[40px] grow" />
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      {!canReply ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3 py-2.5 text-sm text-muted">
          Your role can read chats but not reply to them.
        </p>
      ) : open ? (
        <form
          aria-label="Reply"
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            onSend();
          }}
        >
          {savedReplies.length > 0 ? (
            <div role="group" aria-label="Saved replies" className="flex flex-wrap gap-2">
              {savedReplies.map((reply) => (
                <Button key={reply.id} variant="secondary" className="px-3.5" onClick={() => onDraft(reply.body)}>
                  {reply.title}
                </Button>
              ))}
            </div>
          ) : null}
          <div className="flex items-end gap-2.5">
            <label className="grow">
              <span className="sr-only">Reply</span>
              <textarea
                value={draft}
                rows={draft.includes("\n") || draft.length > 70 ? 3 : 1}
                placeholder="Write a reply"
                aria-invalid={tooLong}
                onChange={(event) => onDraft(event.target.value)}
                className="block min-h-12 w-full resize-y rounded-[24px] border border-border-strong bg-surface px-4 py-3 text-md font-medium text-ink placeholder:text-muted aria-invalid:border-danger"
              />
            </label>
            <Button
              type="submit"
              variant="accent"
              className="h-12 px-5 text-md"
              disabled={busy !== null || draft.trim().length === 0 || tooLong}
            >
              {busy === "reply" ? "Sending" : "Send"}
            </Button>
          </div>
          {tooLong ? (
            <p className="m-0 text-xs font-bold text-danger">
              Keep it under {MAX_MESSAGE.toLocaleString("en-GB")} characters. This one has {draft.length.toLocaleString("en-GB")}.
            </p>
          ) : null}
        </form>
      ) : null}
    </section>
  );
}

/** The client's account beside the chat. Only what the directory knows: money figures come with the clients screen. */
export function AboutClient({ card }: { card: ClientCard | null | undefined }) {
  return (
    <section
      aria-label="About this client"
      className="flex flex-col gap-2 rounded-[20px] border border-border bg-surface p-[18px]"
    >
      <h2 className="m-0 text-[15px] font-extrabold">About this client</h2>
      {card === undefined ? (
        <p className="m-0 text-sm text-muted">Looking up the client</p>
      ) : card === null ? (
        <p className="m-0 text-sm text-muted">Their details are not available here yet. Look them up in Clients.</p>
      ) : (
        <>
          <p className="m-0 text-sm text-muted">
            {card.accountNumber ? `Account ${card.accountNumber}, ` : "No account number yet, "}
            {card.verified ? "verified" : "not verified yet"}, client since {longDate(card.clientSince)}
          </p>
          {card.status === "locked" || card.status === "closed" ? (
            <p className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
              {card.status === "locked"
                ? "Their account is locked. They cannot sign in to read a reply, and Finvest sends them no messages."
                : "Their account is closed. They cannot sign in to read a reply."}
            </p>
          ) : null}
          {card.verified ? null : (
            <p className="m-0 rounded-md bg-warning-surface px-3 py-2.5 text-sm font-semibold text-warning-text">
              Their identity check has not passed, so they cannot invest or withdraw yet.
            </p>
          )}
        </>
      )}
      {card ? (
        <Link to={`/clients?client=${encodeURIComponent(card.clientId)}`} className="flex h-11 items-center text-sm font-bold">
          Open full client record
        </Link>
      ) : null}
    </section>
  );
}
