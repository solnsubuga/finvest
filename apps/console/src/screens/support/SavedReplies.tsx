import { useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { MAX_MESSAGE, MAX_SAVED_TITLE, type SavedReply } from "./api.ts";

interface SavedRepliesProps {
  replies: readonly SavedReply[];
  canReply: boolean;
  busy: string | null;
  error: string | null;
  /** Resolves true once the reply is saved, so the form can clear. */
  onSave: (title: string, body: string) => Promise<boolean>;
  onRetire: (replyId: string) => void;
}

/** Wording the team reuses in chats. Retiring one takes it off the list; chats that used it keep their text. */
export function SavedReplies({ replies, canReply, busy, error, onSave, onRetire }: SavedRepliesProps) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const ready =
    title.trim().length > 0 && title.trim().length <= MAX_SAVED_TITLE && body.trim().length > 0 && body.length <= MAX_MESSAGE;

  return (
    <div className="flex flex-wrap items-start gap-5">
      <section aria-label="Saved replies" className="flex min-w-0 flex-[3_1_420px] flex-col gap-3">
        {replies.length === 0 ? (
          <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
            <h2 className="m-0 text-[17px] font-extrabold">No saved replies</h2>
            <p className="m-0 text-md leading-normal text-muted">
              Save an answer the team gives often, and it shows above the reply box in every chat.
            </p>
          </div>
        ) : (
          replies.map((reply) => (
            <article
              key={reply.id}
              aria-label={reply.title}
              className="flex flex-col gap-2 rounded-[16px] border border-border bg-surface px-4 py-3.5"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="m-0 text-md font-bold">{reply.title}</h2>
                {canReply ? (
                  <Button variant="secondary" className="px-3.5" disabled={busy !== null} onClick={() => onRetire(reply.id)}>
                    {busy === reply.id ? "Retiring" : "Retire"}
                  </Button>
                ) : null}
              </div>
              <p className="m-0 whitespace-pre-wrap text-sm leading-normal text-muted">{reply.body}</p>
            </article>
          ))
        )}
      </section>
      {canReply ? (
        <form
          aria-label="Save a reply"
          className="flex min-w-0 flex-[2_1_300px] flex-col gap-3 rounded-[20px] border border-border bg-surface p-5"
          onSubmit={(event) => {
            event.preventDefault();
            void onSave(title, body).then((saved) => {
              if (saved) {
                setTitle("");
                setBody("");
              }
            });
          }}
        >
          <h2 className="m-0 text-[17px] font-extrabold">Save a reply</h2>
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            Name on the button
            <Input
              value={title}
              maxLength={MAX_SAVED_TITLE}
              onChange={(event) => setTitle(event.target.value)}
              className="h-11 text-md"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            What it says
            <textarea
              value={body}
              rows={4}
              onChange={(event) => setBody(event.target.value)}
              className="w-full resize-y rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink"
            />
          </label>
          <p className="m-0 text-xs text-muted">
            Write it for any client: no names, amounts or dates. Add those in the chat before sending.
          </p>
          {error ? (
            <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
              {error}
            </p>
          ) : null}
          <Button type="submit" className="self-start px-[18px]" disabled={!ready || busy !== null}>
            {busy === "new" ? "Saving" : "Save reply"}
          </Button>
        </form>
      ) : null}
    </div>
  );
}
