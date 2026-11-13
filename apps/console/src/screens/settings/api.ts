/**
 * The console's view of the words in client messages.
 *
 * The shapes follow the wording routes in docs/notifications.md. `http.ts`
 * calls them; `stub.ts` is the sample for `pnpm dev` and previews.
 */

export type Category = "money" | "investments" | "earnings" | "account" | "security" | "support" | "news";

export interface WordingText {
  title: string;
  /** Shown in the app, in a push notification and in an email. */
  body: string;
  /** The shorter text sent by SMS. */
  sms: string;
}

export interface Proposal {
  id: string;
  wording: WordingText;
  /** The words with sample values filled in. */
  preview: WordingText;
  reason: string;
  proposedBy: string;
  proposedAt: string;
  /** The signed-in member of staff proposed it, so they can withdraw it but not decide it. */
  yours: boolean;
}

export interface Wording {
  event: string;
  category: Category;
  /** The placeholders this message may use, without braces. */
  variables: readonly string[];
  wording: WordingText;
  preview: WordingText;
  /** Staff have changed the words from the ones Finvest shipped with. */
  edited: boolean;
  changedAt: string | null;
  /** A change waiting for a second person. At most one per message. */
  proposal: Proposal | null;
}

export type Decision = "approved" | "rejected" | "withdrawn";

export type Field = "title" | "body" | "sms";
export type ProblemCode =
  | "required"
  | "too_long"
  | "invalid_characters"
  | "unknown_placeholder"
  | "repeated_placeholder"
  | "stray_brace"
  | "outside_link"
  | "no_rates";

export interface Problem {
  field: Field;
  code: ProblemCode;
}

export interface WordingsApi {
  /** Every message a client can be sent whose words staff may change. */
  list(): Promise<Wording[]>;
  propose(event: string, wording: WordingText, reason: string): Promise<Wording>;
  /** `approved` or `rejected` by someone other than the proposer, `withdrawn` by the proposer. */
  decide(proposalId: string, decision: Decision, reason: string): Promise<Wording>;
}

export type WordingsErrorCode = "invalid" | "conflict" | "same_person" | "forbidden" | "not_found" | "not_connected" | "unavailable";

export class WordingsError extends Error {
  constructor(
    readonly code: WordingsErrorCode,
    /** For `invalid`: what the backend refused, field by field. */
    readonly problems: readonly Problem[] = [],
  ) {
    super(code);
    this.name = "WordingsError";
  }
}

const MESSAGES: Record<WordingsErrorCode, string> = {
  invalid: "Finvest refused these words. Fix what is marked and send them again.",
  conflict: "Someone else changed this message first. Here it is as it now stands.",
  same_person: "A change needs a second person. Someone other than you must decide it.",
  forbidden: "Your role can read these messages but not change them.",
  not_found: "This change has already been decided.",
  not_connected: "Client messages are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function wordingsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof WordingsError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new WordingsError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedWordingsApi: WordingsApi = {
  list: notConnected,
  propose: notConnected,
  decide: notConnected,
};
