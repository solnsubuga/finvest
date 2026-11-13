/**
 * The help chat over the real API (`/staff/support/…`, docs/support.md).
 */

import { ApiError, type Http } from "@/api/http.ts";
import {
  SupportError,
  type Author,
  type Chat,
  type Conversation,
  type ConversationStatus,
  type InboxRow,
  type Message,
  type SavedReply,
  type SupportApi,
  type Topic,
} from "./api.ts";

interface ConversationJson {
  id: string;
  topic: Topic;
  status: ConversationStatus;
  created_at: string;
  last_message_at: string;
  waiting_since: string | null;
}

interface MessageJson {
  id: string;
  conversation_id: string;
  author: Author;
  author_name: string | null;
  body: string;
  created_at: string;
}

interface InboxRowJson {
  conversation: ConversationJson;
  client_id: string;
  last_message: string;
  last_author: Author;
  unread: number;
}

export function toConversation(json: ConversationJson): Conversation {
  return {
    id: json.id,
    topic: json.topic,
    status: json.status,
    createdAt: json.created_at,
    lastMessageAt: json.last_message_at,
    waitingSince: json.waiting_since,
  };
}

export function toMessage(json: MessageJson): Message {
  return {
    id: json.id,
    conversationId: json.conversation_id,
    author: json.author,
    authorName: json.author_name,
    body: json.body,
    createdAt: json.created_at,
  };
}

export function toSupportError(error: unknown): SupportError {
  if (error instanceof SupportError) return error;
  if (!(error instanceof ApiError)) return new SupportError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new SupportError("invalid");
    case "not_found":
      return new SupportError("not_found");
    // Replying to or solving a chat that is already solved.
    case "conflict":
      return new SupportError("conflict");
    case "forbidden":
      return new SupportError("forbidden");
    default:
      return new SupportError("unavailable");
  }
}

export function createHttpSupportApi(http: Http): SupportApi {
  const chatPath = (id: string, action = "") => `/staff/support/conversations/${encodeURIComponent(id)}${action}`;
  const call = async <T>(run: () => Promise<T>): Promise<T> => {
    try {
      return await run();
    } catch (error) {
      throw toSupportError(error);
    }
  };

  return {
    inbox: (status) =>
      call(async () => {
        const json = await http.request<{ conversations: InboxRowJson[] }>(
          "GET",
          `/staff/support/conversations?${new URLSearchParams({ status }).toString()}`,
        );
        return json.conversations.map(
          (row): InboxRow => ({
            conversation: toConversation(row.conversation),
            clientId: row.client_id,
            lastMessage: row.last_message,
            lastAuthor: row.last_author,
            unread: row.unread,
          }),
        );
      }),
    chat: (conversationId) =>
      call(async () => {
        const json = await http.request<{ conversation: ConversationJson; client_id: string; messages: MessageJson[] }>(
          "GET",
          chatPath(conversationId),
        );
        return { conversation: toConversation(json.conversation), clientId: json.client_id, messages: json.messages.map(toMessage) } satisfies Chat;
      }),
    reply: (conversationId, body, key) =>
      call(async () => toMessage(await http.request<MessageJson>("POST", chatPath(conversationId, "/messages"), { body, key }))),
    solve: (conversationId) =>
      call(async () => toConversation(await http.request<ConversationJson>("POST", chatPath(conversationId, "/solve")))),
    setTopic: (conversationId, topic) =>
      call(async () => toConversation(await http.request<ConversationJson>("PUT", chatPath(conversationId, "/topic"), { topic }))),
    savedReplies: () =>
      call(async () => (await http.request<{ replies: SavedReply[] }>("GET", "/staff/support/saved-replies")).replies.map((reply) => ({ ...reply }))),
    saveReply: (title, body) =>
      call(async () => {
        const reply = await http.request<SavedReply>("POST", "/staff/support/saved-replies", { title, body });
        return { id: reply.id, title: reply.title, body: reply.body };
      }),
    retireReply: (replyId) =>
      call(async () => {
        await http.request<void>("DELETE", `/staff/support/saved-replies/${encodeURIComponent(replyId)}`);
      }),
  };
}
