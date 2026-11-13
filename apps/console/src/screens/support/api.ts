/**
 * The console's view of the help chat.
 *
 * The shapes follow the staff routes in docs/support.md. `http.ts` calls them;
 * `stub.ts` is the sample for `pnpm dev` and previews.
 */

export type Topic = "deposit" | "withdrawal" | "investment" | "identity" | "account" | "other";
export type ConversationStatus = "open" | "solved";
export type Author = "client" | "staff";

export interface Conversation {
  id: string;
  topic: Topic;
  status: ConversationStatus;
  createdAt: string;
  lastMessageAt: string;
  /** When the client last wrote with no answer since. Null once staff have replied. */
  waitingSince: string | null;
}

export interface InboxRow {
  conversation: Conversation;
  clientId: string;
  lastMessage: string;
  lastAuthor: Author;
  unread: number;
}

export interface Message {
  id: string;
  conversationId: string;
  author: Author;
  /** A member of staff's first name. Null for the client's own messages. */
  authorName: string | null;
  body: string;
  createdAt: string;
}

export interface Chat {
  conversation: Conversation;
  clientId: string;
  messages: readonly Message[];
}

export interface SavedReply {
  id: string;
  title: string;
  body: string;
}

export const MAX_MESSAGE = 2000;
export const MAX_SAVED_TITLE = 60;

export interface SupportApi {
  /** Open chats come with the client who has waited longest first. */
  inbox(status: ConversationStatus): Promise<InboxRow[]>;
  /** Opening a chat marks it read by staff. */
  chat(conversationId: string): Promise<Chat>;
  /** `key` is made once per message, so a retry after a lost answer posts it once. */
  reply(conversationId: string, body: string, key: string): Promise<Message>;
  solve(conversationId: string): Promise<Conversation>;
  setTopic(conversationId: string, topic: Topic): Promise<Conversation>;
  savedReplies(): Promise<SavedReply[]>;
  saveReply(title: string, body: string): Promise<SavedReply>;
  retireReply(replyId: string): Promise<void>;
}

export type SupportErrorCode = "invalid" | "not_found" | "conflict" | "forbidden" | "not_connected" | "unavailable";

export class SupportError extends Error {
  constructor(readonly code: SupportErrorCode) {
    super(code);
    this.name = "SupportError";
  }
}

const MESSAGES: Record<SupportErrorCode, string> = {
  invalid: "That could not be sent. Check the message and try again.",
  not_found: "This chat no longer exists.",
  conflict: "This chat is already solved. The client's next message opens a new one.",
  forbidden: "Your role can read chats but not reply to them.",
  not_connected: "Support is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function supportErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof SupportError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new SupportError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedSupportApi: SupportApi = {
  inbox: notConnected,
  chat: notConnected,
  reply: notConnected,
  solve: notConnected,
  setTopic: notConnected,
  savedReplies: notConnected,
  saveReply: notConnected,
  retireReply: notConnected,
};
