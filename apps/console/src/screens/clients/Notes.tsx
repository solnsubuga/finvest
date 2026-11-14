import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { formatTime } from "./labels.ts";
import { INPUT } from "./Parts.tsx";
import { MAX_NOTE, noteErrorMessage, type ClientNotes, type NotesApi } from "./notes.ts";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; found: ClientNotes };

/** Staff notes on the client: read by every member of staff, never shown to the client. */
export function NotesSection({ api, clientId }: { api: NotesApi; clientId: string }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [body, setBody] = useState("");
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  const fetchNotes = useCallback(
    (): Promise<Load> =>
      api.notesFor(clientId).then(
        (found) => ({ state: "ready", found }),
        (failure: unknown) => ({
          state: "failed",
          message: noteErrorMessage(failure),
        }),
      ),
    [api, clientId],
  );

  useEffect(() => {
    let current = true;
    void fetchNotes().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchNotes]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setTried(true);
    if (!body.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.write(clientId, body);
      setBody("");
      setTried(false);
      setLoad(await fetchNotes());
    } catch (failure) {
      setError(noteErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  const missing = tried && !body.trim();
  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id={`${id}-title`} className="m-0 text-xl font-extrabold">
          Staff notes
        </h2>
        <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
          What one of us wrote for the others. The client never sees these. A note is kept as written, so put a mistake right with a later note.
        </p>
      </div>
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-sm text-muted">
          Loading notes
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex max-w-[720px] flex-col items-start gap-2 rounded-xl border border-border bg-surface p-4">
          <p className="m-0 text-md text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchNotes().then(setLoad)}>
            Try again
          </Button>
        </div>
      ) : (
        <div className="flex max-w-[720px] flex-col gap-3">
          {load.found.canWrite ? (
            <form
              aria-label="Write a note"
              onSubmit={(event) => void submit(event)}
              className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-4"
            >
              <label htmlFor={`${id}-body`} className="flex flex-col gap-1.5 text-sm font-bold">
                New note
                <span className="text-xs font-medium text-muted">
                  Keep it to what the next person needs. Never write a PIN, a code or a full account number.
                </span>
                <textarea
                  id={`${id}-body`}
                  value={body}
                  rows={3}
                  maxLength={MAX_NOTE}
                  aria-invalid={missing}
                  onChange={(event) => setBody(event.target.value)}
                  className={INPUT}
                />
                {missing ? <span className="text-xs font-bold text-danger">Write the note first.</span> : null}
              </label>
              {error ? (
                <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
                  {error}
                </p>
              ) : null}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs text-muted tabular-nums">
                  {body.length} of {MAX_NOTE}
                </span>
                <Button type="submit" disabled={busy}>
                  {busy ? "Saving" : "Save note"}
                </Button>
              </div>
            </form>
          ) : null}
          {load.found.notes.length === 0 ? (
            <p className="m-0 rounded-xl border border-border bg-surface p-4 text-sm text-muted">No notes on this client yet.</p>
          ) : (
            <ol aria-label="Notes, newest first" className="m-0 flex list-none flex-col gap-2 p-0">
              {load.found.notes.map((note) => (
                <li key={note.id} className="flex flex-col gap-1 rounded-xl border border-border bg-surface p-4">
                  <p className="m-0 whitespace-pre-wrap break-words text-md">{note.body}</p>
                  <p className="m-0 text-xs text-muted">
                    {note.writtenByName}, {formatTime(note.writtenAt)}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}
