/**
 * The contract between the notification, help and complaint screens and
 * whatever answers them.
 *
 * Today the only implementation is the in-memory demo in
 * `stubSupportClient.ts`. The shapes follow the backend routes in
 * docs/notifications.md and docs/support.md, so an implementation backed by
 * the generated `packages/api-client` types replaces the demo and the screens
 * do not change.
 */

export type Category =
  | "money"
  | "investments"
  | "earnings"
  | "account"
  | "security"
  | "support"
  | "news";

export type Channel = "push" | "sms" | "email";

/** The screen a notification opens, named by kind and ID. Never a web link. */
export type LinkKind =
  | "deposit"
  | "payout"
  | "investment"
  | "identity_check"
  | "payout_destination"
  | "statement"
  | "security"
  | "conversation"
  | "complaint"
  | "goal"
  | "autoinvest_plan"
  | "organisation";

export type Notification = {
  id: string;
  category: Category;
  title: string;
  body: string;
  link: { kind: LinkKind; id: string } | null;
  /** ISO 8601, UTC. */
  createdAt: string;
  read: boolean;
};

export type NotificationPage = {
  items: Notification[];
  unread: number;
  /** Pass as `before` to read older ones. Null on the last page. */
  nextBefore: string | null;
};

export type CategoryChannels = {
  category: Category;
  channels: Channel[];
  /** True when SMS or email must stay on. Security alerts cannot be silenced. */
  alwaysOn: boolean;
};

export type Topic = "deposit" | "withdrawal" | "investment" | "identity" | "account" | "other";

export type Message = {
  id: string;
  conversationId: string;
  author: "client" | "staff";
  /** A member of staff's first name, such as "Grace". Null for the client's own messages. */
  authorName: string | null;
  body: string;
  /** ISO 8601, UTC. */
  createdAt: string;
};

export type Chat = {
  /** The open conversation, or the last one. Null before the first message. */
  conversation: { id: string; status: "open" | "solved" } | null;
  messages: Message[];
  /** Replies the client has not opened Help to read yet. */
  unread: number;
};

export type OutgoingMessage = {
  body: string;
  /**
   * Made up once for each message and sent again unchanged on a retry, so a
   * message whose answer was lost is never posted twice.
   */
  key: string;
  topic: Topic;
};

export type ComplaintCategory =
  | "payments"
  | "withdrawals"
  | "investments"
  | "identity"
  | "account"
  | "fees"
  | "service"
  | "other";

export type ComplaintStatus = "new" | "investigating" | "waiting_for_client" | "escalated" | "closed";

export type ComplaintOutcome = "upheld" | "partly_upheld" | "not_upheld";

export type Complaint = {
  id: string;
  /** "C-1001". What the client quotes when they call. */
  reference: string;
  category: ComplaintCategory;
  summary: string;
  details: string;
  status: ComplaintStatus;
  /** ISO 8601, UTC. */
  receivedAt: string;
  /** The day by which Finvest promised to answer. `YYYY-MM-DD`. */
  dueOn: string;
  closedAt: string | null;
  outcome: ComplaintOutcome | null;
  /** What Finvest did about it. Set when the complaint is closed. */
  resolution: string | null;
};

export type NewComplaint = {
  category: ComplaintCategory;
  summary: string;
  details: string;
  /** Made once per complaint form and sent again on every try, so a lost answer never logs it twice. */
  key: string;
};

export const MAX_MESSAGE = 2000;
export const MAX_SUMMARY = 120;
export const MAX_DETAILS = 4000;

export type SupportErrorCode =
  /** A field was refused. The screens check first, so it is rare. */
  | "invalid"
  | "not_found"
  | "unavailable";

export class SupportError extends Error {
  readonly code: SupportErrorCode;

  constructor(code: SupportErrorCode) {
    super(code);
    this.name = "SupportError";
    this.code = code;
  }
}

export interface SupportClient {
  listNotifications(before?: string): Promise<NotificationPage>;
  /** Marks the given notifications read, or all of them. Answers with what is left unread. */
  markNotificationsRead(ids?: string[]): Promise<number>;
  getPreferences(): Promise<CategoryChannels[]>;
  savePreferences(category: Category, channels: Channel[]): Promise<CategoryChannels[]>;

  /** The chat. With `after`, only messages newer than that message ID. */
  getChat(after?: string): Promise<Chat>;
  sendMessage(message: OutgoingMessage): Promise<Message>;
  markChatRead(): Promise<void>;

  listComplaints(): Promise<Complaint[]>;
  getComplaint(complaintId: string): Promise<Complaint>;
  makeComplaint(complaint: NewComplaint): Promise<Complaint>;
}

const messages: Record<SupportErrorCode, string> = {
  invalid: "Something in what you wrote could not be accepted. Check it and try again.",
  not_found: "We could not find this.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

/** A sentence a client can act on. Never shows codes or technical detail. */
export function describeSupportError(error: unknown): string {
  if (error instanceof SupportError) {
    return messages[error.code];
  }
  return messages.unavailable;
}
