import type { ClientComplaint, MessageItem, NotificationItem } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";

import { Complaint, Message, Notification, SupportClient, SupportError } from "./types";

/**
 * Notifications, help chat and complaints, answered by the real API
 * (backend/app/modules/notifications and backend/app/modules/support).
 */

export function toNotification(item: NotificationItem): Notification {
  return {
    id: item.id,
    category: item.category,
    title: item.title,
    body: item.body,
    link: item.link ? { kind: item.link.kind, id: item.link.id } : null,
    createdAt: item.created_at,
    read: item.read,
  };
}

export function toMessage(item: MessageItem): Message {
  return {
    id: item.id,
    conversationId: item.conversation_id,
    author: item.author,
    authorName: item.author_name,
    body: item.body,
    createdAt: item.created_at,
  };
}

export function toComplaint(item: ClientComplaint): Complaint {
  return {
    id: item.id,
    reference: item.reference,
    category: item.category,
    summary: item.summary,
    details: item.details,
    status: item.status,
    receivedAt: item.received_at,
    dueOn: item.due_on,
    closedAt: item.closed_at,
    outcome: item.outcome,
    resolution: item.resolution,
  };
}

export function toSupportError(error: unknown): SupportError {
  if (error instanceof SupportError) return error;
  if (error instanceof ApiError) {
    if (error.code === "validation_failed") return new SupportError("invalid");
    if (error.code === "not_found") return new SupportError("not_found");
  }
  return new SupportError("unavailable");
}

async function wrap<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toSupportError(error);
  }
}

export function createApiSupportClient(): SupportClient {
  return {
    listNotifications: (before) =>
      wrap(async () => {
        const page = await api("GET /client/notifications", { query: { before: before ?? null } });
        return { items: page.items.map(toNotification), unread: page.unread, nextBefore: page.next_before };
      }),

    markNotificationsRead: (ids) =>
      wrap(async () => (await api("POST /client/notifications/read", { body: { ids: ids ?? null } })).unread),

    getPreferences: () =>
      wrap(async () =>
        (await api("GET /client/notification-preferences", {})).categories.map((item) => ({
          category: item.category,
          channels: item.channels,
          alwaysOn: item.always_on,
        })),
      ),

    savePreferences: (category, channels) =>
      wrap(async () =>
        (
          await api("PUT /client/notification-preferences", { body: { categories: [{ category, channels }] } })
        ).categories.map((item) => ({ category: item.category, channels: item.channels, alwaysOn: item.always_on })),
      ),

    getChat: (after) =>
      wrap(async () => {
        const chat = await api("GET /client/support/chat", { query: { after: after ?? null } });
        return {
          conversation: chat.conversation ? { id: chat.conversation.id, status: chat.conversation.status } : null,
          messages: chat.messages.map(toMessage),
          unread: chat.unread,
        };
      }),

    // The key is made once per message on the screen, so a retry after a lost answer is sent once.
    sendMessage: (message) =>
      wrap(async () =>
        toMessage(
          await api("POST /client/support/messages", {
            body: { body: message.body, key: message.key, topic: message.topic },
          }),
        ),
      ),

    markChatRead: () =>
      wrap(async () => {
        await api("POST /client/support/read", {});
      }),

    listComplaints: () =>
      wrap(async () => (await api("GET /client/complaints", {})).complaints.map(toComplaint)),

    getComplaint: (complaintId) =>
      wrap(async () => toComplaint(await api("GET /client/complaints/{complaint_id}", { params: { complaint_id: complaintId } }))),

    makeComplaint: (complaint) =>
      wrap(async () =>
        toComplaint(
          await api("POST /client/complaints", {
            body: { category: complaint.category, summary: complaint.summary, details: complaint.details },
          }),
        ),
      ),
  };
}
