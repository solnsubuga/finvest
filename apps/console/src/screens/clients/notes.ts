/**
 * Staff notes on a client (`/staff/clients/{id}/notes` in
 * backend/app/modules/console/actions_api.py): what one member of staff wrote for the others,
 * such as "Called about a failed payout, resent it".
 *
 * A note is written by one person under their own name and kept as written. Nothing edits or
 * deletes one, so a mistake is put right by a later note. Only staff read notes, and the
 * client never sees them. Reading is `clients.view`; writing is `client_notes.write`.
 */

import { ApiError, type Http } from "@/api/http.ts";

export const MAX_NOTE = 1000;

export interface Note {
  id: string;
  body: string;
  writtenByName: string;
  writtenAt: string;
}

export interface ClientNotes {
  /** Newest first. */
  notes: Note[];
  canWrite: boolean;
}

export interface NotesApi {
  notesFor(clientId: string): Promise<ClientNotes>;
  write(clientId: string, body: string): Promise<Note>;
}

export type NoteErrorCode = "invalid" | "no_client" | "forbidden" | "not_connected" | "unavailable";

export class NoteError extends Error {
  readonly code: NoteErrorCode;

  constructor(code: NoteErrorCode) {
    super(code);
    this.name = "NoteError";
    this.code = code;
  }
}

const MESSAGES: Record<NoteErrorCode, string> = {
  invalid: `Write the note, in at most ${MAX_NOTE} characters.`,
  no_client: "Finvest could not find this client. Reload the page.",
  forbidden: "Your role cannot write notes on a client.",
  not_connected: "Staff notes are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function noteErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof NoteError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new NoteError("not_connected"));

export const notConnectedNotesApi: NotesApi = {
  notesFor: notConnected,
  write: notConnected,
};

// Over HTTP ---------------------------------------------------------------------------------------

interface NoteJson {
  id: string;
  body: string;
  written_by_name: string;
  written_at: string;
}

function toNote(json: NoteJson): Note {
  return {
    id: json.id,
    body: json.body,
    writtenByName: json.written_by_name,
    writtenAt: json.written_at,
  };
}

export function toNoteError(error: unknown): NoteError {
  if (error instanceof NoteError) return error;
  if (!(error instanceof ApiError)) return new NoteError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new NoteError("invalid");
    case "not_found":
      return new NoteError("no_client");
    case "forbidden":
      return new NoteError("forbidden");
    default:
      return new NoteError("unavailable");
  }
}

export function createHttpNotesApi(http: Http): NotesApi {
  const path = (clientId: string) => `/staff/clients/${encodeURIComponent(clientId)}/notes`;
  return {
    notesFor: async (clientId) => {
      try {
        const json = await http.request<{
          notes: NoteJson[];
          can_write: boolean;
        }>("GET", path(clientId));
        return { notes: json.notes.map(toNote), canWrite: json.can_write };
      } catch (error) {
        throw toNoteError(error);
      }
    },
    write: async (clientId, body) => {
      try {
        return toNote(await http.request<NoteJson>("POST", path(clientId), { body }));
      } catch (error) {
        throw toNoteError(error);
      }
    },
  };
}

// Samples -----------------------------------------------------------------------------------------

/** Sample notes for `pnpm dev` and previews. `empty` starts with none. */
export function createStubNotesApi(
  me: { name: string },
  canWrite: boolean,
  sample: "sample" | "empty" = "sample",
  now: () => number = Date.now,
  delayMs = 120,
): NotesApi {
  const hour = 3_600_000;
  const notes: Note[] =
    sample === "empty"
      ? []
      : [
          {
            id: "-NstubNote0000000002",
            body: "Client called about a mobile money payout that failed on Tuesday. The network was down; it went through on the retry. Told them.",
            writtenByName: "Grace Auma",
            writtenAt: new Date(now() - 3 * hour).toISOString(),
          },
          {
            id: "-NstubNote0000000001",
            body: "Prefers a call after 5 pm. Works shifts at Mulago.",
            writtenByName: "Ruth Namata",
            writtenAt: new Date(now() - 76 * hour).toISOString(),
          },
        ];
  let next = 3;
  const later = <T>(value: () => T) =>
    new Promise<T>((resolve, reject) =>
      setTimeout(() => {
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new Error(String(error)));
        }
      }, delayMs),
    );
  return {
    notesFor: () => later(() => ({ notes: [...notes], canWrite })),
    write: (_clientId, body) =>
      later(() => {
        if (!canWrite) throw new NoteError("forbidden");
        const cleaned = body.trim();
        if (!cleaned || cleaned.length > MAX_NOTE) throw new NoteError("invalid");
        const note = {
          id: `-NstubNote${String(next++).padStart(10, "0")}`,
          body: cleaned,
          writtenByName: me.name,
          writtenAt: new Date(now()).toISOString(),
        };
        notes.unshift(note);
        return note;
      }),
  };
}
