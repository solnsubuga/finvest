import { useCallback, useEffect, useRef, useState } from "react";
import { AppState } from "react-native";
import { randomUUID } from "expo-crypto";

import { useDataSaving } from "@/dataSaving/DataSavingProvider";

import { CHAT_POLL_MS, LIGHT_CHAT_POLL_MS } from "./pollInterval";
import { useSupport } from "./SupportProvider";
import { MAX_MESSAGE, Message, Topic } from "./types";

/** A message the client wrote that is not confirmed as sent yet. */
export type Outgoing = {
  key: string;
  body: string;
  topic: Topic;
  state: "sending" | "failed";
};

export type ChatView = {
  status: "loading" | "ready" | "failed";
  messages: Message[];
  outgoing: Outgoing[];
  /** True when the latest read failed. What is on screen is still shown. */
  stale: boolean;
  send: (body: string, topic: Topic) => void;
  retry: (key: string) => void;
  reload: () => void;
};

function newKey(): string {
  const key: unknown = randomUUID();
  // Without a key a retry could post the same message twice.
  if (typeof key !== "string" || key.length < 16) {
    throw new Error("Could not make a message key");
  }
  return key;
}

/**
 * The help chat while its screen is in front. It reads the whole chat once,
 * then asks only for newer messages every few seconds. It stops when the
 * screen is left or the app goes to the background, and reads again on return.
 *
 * A message is shown at once as "Sending". It keeps its key, so trying again
 * after a lost answer can never post it twice.
 */
export function useChat(active: boolean): ChatView {
  const { client } = useSupport();
  const { light } = useDataSaving();
  const pace = light ? LIGHT_CHAT_POLL_MS : CHAT_POLL_MS;
  const [status, setStatus] = useState<ChatView["status"]>("loading");
  const [messages, setMessages] = useState<Message[]>([]);
  const [outgoing, setOutgoing] = useState<Outgoing[]>([]);
  const [stale, setStale] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const lastId = useRef<string | undefined>(undefined);
  const loaded = useRef(false);

  const merge = useCallback((incoming: Message[]) => {
    if (incoming.length === 0) {
      return;
    }
    setMessages((current) => {
      const known = new Set(current.map((message) => message.id));
      const fresh = incoming.filter((message) => !known.has(message.id));
      if (fresh.length === 0) {
        return current;
      }
      const next = [...current, ...fresh];
      lastId.current = (next[next.length - 1] as Message).id;
      return next;
    });
  }, []);

  useEffect(() => {
    if (!active) {
      return;
    }
    let live = true;
    let reading = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function read() {
      if (reading) {
        return;
      }
      reading = true;
      try {
        const chat = await client.getChat(loaded.current ? lastId.current : undefined);
        if (!live) {
          return;
        }
        merge(chat.messages);
        loaded.current = true;
        setStatus("ready");
        setStale(false);
        if (chat.unread > 0) {
          // The client is looking at the chat, so its replies are read.
          void client.markChatRead().catch(() => undefined);
        }
      } catch {
        if (!live) {
          return;
        }
        if (loaded.current) {
          setStale(true);
        } else {
          setStatus("failed");
        }
      } finally {
        reading = false;
      }
      if (live && AppState.currentState !== "background") {
        timer = setTimeout(() => void read(), pace);
      }
    }

    const subscription = AppState.addEventListener("change", (state) => {
      clearTimeout(timer);
      if (state === "active") {
        void read();
      }
    });

    void read();
    return () => {
      live = false;
      subscription.remove();
      clearTimeout(timer);
    };
  }, [active, client, merge, attempt, pace]);

  const post = useCallback(
    async (item: Outgoing) => {
      try {
        const message = await client.sendMessage({
          body: item.body,
          key: item.key,
          topic: item.topic,
        });
        merge([message]);
        setOutgoing((current) => current.filter((candidate) => candidate.key !== item.key));
      } catch {
        setOutgoing((current) =>
          current.map((candidate) =>
            candidate.key === item.key ? { ...candidate, state: "failed" } : candidate,
          ),
        );
      }
    },
    [client, merge],
  );

  const send = useCallback(
    (body: string, topic: Topic) => {
      const text = body.trim();
      if (text.length === 0 || text.length > MAX_MESSAGE) {
        return;
      }
      const item: Outgoing = { key: newKey(), body: text, topic, state: "sending" };
      setOutgoing((current) => [...current, item]);
      void post(item);
    },
    [post],
  );

  const retry = useCallback(
    (key: string) => {
      const item = outgoing.find((candidate) => candidate.key === key);
      if (!item || item.state !== "failed") {
        return;
      }
      const again: Outgoing = { ...item, state: "sending" };
      setOutgoing((current) =>
        current.map((candidate) => (candidate.key === key ? again : candidate)),
      );
      void post(again);
    },
    [outgoing, post],
  );

  const reload = useCallback(() => {
    setStatus("loading");
    setAttempt((value) => value + 1);
  }, []);

  return { status, messages, outgoing, stale, send, retry, reload };
}
