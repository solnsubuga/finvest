import { useCallback, useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { StackHeader } from "@/components/StackHeader";
import { Txt } from "@/components/Txt";
import { shortWhen } from "@/support/format";
import { hrefFor } from "@/support/links";
import { useSupport } from "@/support/SupportProvider";
import { useNow } from "@/support/useNow";
import { describeSupportError, Notification } from "@/support/types";
import { colors } from "@/theme";

type View_ =
  | { status: "loading" }
  | { status: "failed"; message: string }
  | { status: "ready"; items: Notification[]; nextBefore: string | null };

/** The notification centre: everything Finvest told this client, newest first. */
export default function Notifications() {
  const router = useRouter();
  const { client, unread, setUnread } = useSupport();
  const [view, setView] = useState<View_>({ status: "loading" });
  const [loadingMore, setLoadingMore] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    client
      .listNotifications()
      .then((page) => {
        if (live) {
          setView({ status: "ready", items: page.items, nextBefore: page.nextBefore });
          setUnread(page.unread);
        }
      })
      .catch((error: unknown) => {
        if (live) {
          setView({ status: "failed", message: describeSupportError(error) });
        }
      });
    return () => {
      live = false;
    };
  }, [client, setUnread, attempt]);

  const markLocally = useCallback((ids: string[] | null) => {
    setView((current) =>
      current.status === "ready"
        ? {
            ...current,
            items: current.items.map((item) =>
              ids === null || ids.includes(item.id) ? { ...item, read: true } : item,
            ),
          }
        : current,
    );
  }, []);

  const open = async (item: Notification) => {
    setProblem(null);
    if (!item.read) {
      markLocally([item.id]);
      // Reading is best effort. A failure leaves it unread for next time.
      client
        .markNotificationsRead([item.id])
        .then(setUnread)
        .catch(() => undefined);
    }
    const href = hrefFor(item.link);
    if (href) {
      router.push(href);
    }
  };

  const markAll = async () => {
    setProblem(null);
    try {
      setUnread(await client.markNotificationsRead());
      markLocally(null);
    } catch (error) {
      setProblem(describeSupportError(error));
    }
  };

  const more = async () => {
    if (view.status !== "ready" || !view.nextBefore) {
      return;
    }
    setLoadingMore(true);
    setProblem(null);
    try {
      const page = await client.listNotifications(view.nextBefore);
      setView({
        status: "ready",
        items: [...view.items, ...page.items],
        nextBefore: page.nextBefore,
      });
    } catch (error) {
      setProblem(describeSupportError(error));
    } finally {
      setLoadingMore(false);
    }
  };

  const nowMs = useNow();

  return (
    <Screen
      signedIn
      footer={
        <Txt size={13} color={colors.muted} center>
          You choose the channel for each kind of alert: push, SMS or email.
        </Txt>
      }
    >
      <Stack.Screen options={{ title: "Notifications" }} />
      <StackHeader
        title="Notifications"
        fallback="/"
        action={
          <TextLink
            label="Settings"
            underline={false}
            onPress={() => router.push("/notifications/settings")}
          />
        }
      />
      <View style={{ marginTop: 20, gap: 10 }}>
        {view.status === "loading" ? <Loading label="Loading your notifications" /> : null}
        {view.status === "failed" ? (
          <LoadFailed
            message={view.message}
            onRetry={() => {
              setView({ status: "loading" });
              setAttempt((value) => value + 1);
            }}
          />
        ) : null}
        {view.status === "ready" && view.items.length === 0 ? (
          <Notice tone="neutral">
            Nothing yet. We will tell you here when a payment arrives, an investment matures or we
            reply to you.
          </Notice>
        ) : null}
        {problem ? <Notice tone="danger">{problem}</Notice> : null}
        {view.status === "ready" && (unread ?? 0) > 0 ? (
          <View style={{ alignItems: "flex-start" }}>
            <TextLink label="Mark all as read" onPress={markAll} />
          </View>
        ) : null}
        {view.status === "ready"
          ? view.items.map((item) => (
              <Pressable
                key={item.id}
                accessibilityRole="button"
                accessibilityLabel={`${item.read ? "" : "Unread. "}${item.title}. ${item.body}`}
                onPress={() => void open(item)}
                style={({ pressed }) => [styles.item, pressed && { opacity: 0.7 }]}
                testID={`notification-${item.id}`}
              >
                <View
                  style={[styles.dot, { backgroundColor: item.read ? colors.borderStrong : colors.black }]}
                />
                <View style={{ flex: 1, gap: 2 }}>
                  <Txt size={14} weight="bold" lineHeight={19}>
                    {item.title}
                  </Txt>
                  <Txt size={13} lineHeight={18} color={colors.muted}>
                    {item.body}
                  </Txt>
                </View>
                <Txt size={12} color={colors.muted}>
                  {shortWhen(item.createdAt, nowMs)}
                </Txt>
              </Pressable>
            ))
          : null}
        {view.status === "ready" && view.nextBefore ? (
          <Button label="Show older" onPress={more} loading={loadingMore} height={48} />
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  item: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 18,
    paddingVertical: 14,
    paddingHorizontal: 16,
  },
  dot: { marginTop: 6, width: 10, height: 10, borderRadius: 5 },
});
