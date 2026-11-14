import { ComponentRef, useCallback, useEffect, useRef, useState } from "react";
import {
  KeyboardAvoidingView,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { DemoLabel } from "@/components/DemoLabel";
import { Icon } from "@/components/Icon";
import { LoadFailed, Loading } from "@/components/Loading";
import { Txt } from "@/components/Txt";
import { answers } from "@/support/answers";
import { clockTime, dayHeading, sameDay } from "@/support/format";
import { supportPhone } from "@/support/resolveSupport";
import { MAX_MESSAGE, Message, Topic } from "@/support/types";
import { useChat } from "@/support/useChat";
import { useNow } from "@/support/useNow";
import { colors, fontFor } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

const TOPICS: Topic[] = ["deposit", "withdrawal", "investment", "identity", "account", "other"];

/**
 * Help: quick answers, then one chat with the Finvest team. The chat asks for
 * new messages only while this screen is in front.
 */
export default function Help() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const wide = useIsWide();
  const params = useLocalSearchParams<{ topic?: string }>();
  const topic = TOPICS.includes(params.topic as Topic) ? (params.topic as Topic) : "other";
  const [focused, setFocused] = useState(false);
  const [draft, setDraft] = useState("");
  const scroll = useRef<ComponentRef<typeof ScrollView>>(null);
  const phone = supportPhone();

  useFocusEffect(
    useCallback(() => {
      setFocused(true);
      return () => setFocused(false);
    }, []),
  );

  const chat = useChat(focused);
  const count = chat.messages.length + chat.outgoing.length;

  useEffect(() => {
    // Keep the newest message in view.
    scroll.current?.scrollToEnd({ animated: false });
  }, [count]);

  const send = () => {
    if (draft.trim().length === 0) {
      return;
    }
    chat.send(draft, topic);
    setDraft("");
  };

  const nowMs = useNow();
  const empty = chat.status === "ready" && count === 0;

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: colors.paper }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <StatusBar style="dark" />
      <View
        style={[
          styles.page,
          wide ? styles.wide : { paddingTop: insets.top + 24 },
        ]}
      >
        <DemoLabel signedIn />
        <View style={styles.header}>
          {wide ? null : <BackButton fallback="/profile" />}
          <Txt
            accessibilityRole="header"
            size={wide ? 26 : 20}
            weight="extrabold"
            style={{ flex: 1 }}
          >
            Help
          </Txt>
          {phone ? (
            <Pressable
              accessibilityRole="link"
              onPress={() => void Linking.openURL(`tel:${phone}`)}
              style={({ pressed }) => [styles.pill, pressed && { opacity: 0.7 }]}
            >
              <Txt size={13} weight="semibold">
                Call us
              </Txt>
            </Pressable>
          ) : null}
        </View>
        <View>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={{ gap: 8 }}
          >
            {answers.map((answer) => (
              <Pressable
                key={answer.slug}
                accessibilityRole="link"
                onPress={() =>
                  router.push({ pathname: "/support/answers/[slug]", params: { slug: answer.slug } })
                }
                style={({ pressed }) => [styles.pill, pressed && { opacity: 0.7 }]}
              >
                <Txt size={13} weight="semibold">
                  {answer.chip}
                </Txt>
              </Pressable>
            ))}
            <Pressable
              accessibilityRole="link"
              onPress={() => router.push("/support/complaints")}
              style={({ pressed }) => [styles.pill, pressed && { opacity: 0.7 }]}
              testID="open-complaints"
            >
              <Txt size={13} weight="semibold">
                Complaints
              </Txt>
            </Pressable>
          </ScrollView>
        </View>
        <ScrollView
          ref={scroll}
          style={{ flex: 1 }}
          contentContainerStyle={styles.messages}
          keyboardShouldPersistTaps="handled"
        >
          {chat.status === "loading" ? <Loading label="Loading your chat" /> : null}
          {chat.status === "failed" ? (
            <LoadFailed
              message="We could not load your chat. Check your connection and try again."
              onRetry={chat.reload}
            />
          ) : null}
          {empty ? (
            <View style={styles.intro}>
              <Txt weight="bold">Write to us here</Txt>
              <Txt size={14} color={colors.muted}>
                A member of the Finvest team answers in this chat, and we send you an alert when they
                do. We never ask for your password, your PIN or a code.
              </Txt>
            </View>
          ) : null}
          {chat.messages.map((message, index) => {
            const previous = chat.messages[index - 1];
            const newDay = !previous || !sameDay(previous.createdAt, message.createdAt);
            return (
              <View key={message.id} style={{ gap: 12 }}>
                {newDay ? (
                  <Txt size={12} color={colors.muted} center>
                    {dayHeading(message.createdAt, nowMs)}
                  </Txt>
                ) : null}
                <Bubble message={message} />
              </View>
            );
          })}
          {chat.outgoing.map((item) => (
            <View key={item.key} style={{ alignItems: "flex-end", gap: 4 }}>
              <View style={[styles.bubble, styles.mine, { opacity: 0.6 }]}>
                <Txt size={14} lineHeight={20} color={colors.white}>
                  {item.body}
                </Txt>
              </View>
              {item.state === "failed" ? (
                <Pressable
                  accessibilityRole="button"
                  onPress={() => chat.retry(item.key)}
                  style={{ minHeight: 44, justifyContent: "center" }}
                  testID="retry-message"
                >
                  <Txt size={12} weight="semibold" color={colors.danger}>
                    Not sent. Tap to try again.
                  </Txt>
                </Pressable>
              ) : (
                <Txt size={12} color={colors.muted}>
                  Sending
                </Txt>
              )}
            </View>
          ))}
          {chat.stale ? (
            <Notice tone="warning">
              We cannot reach Finvest right now. New replies will show when the connection is back.
            </Notice>
          ) : null}
        </ScrollView>
        <View style={styles.composer}>
          <TextInput
            accessibilityLabel="Message"
            placeholder="Write a message"
            placeholderTextColor={colors.muted}
            value={draft}
            onChangeText={setDraft}
            onSubmitEditing={send}
            submitBehavior="submit"
            returnKeyType="send"
            maxLength={MAX_MESSAGE}
            editable={chat.status === "ready"}
            style={styles.input}
            testID="chat-input"
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Send"
            aria-disabled={draft.trim().length === 0}
            disabled={draft.trim().length === 0 || chat.status !== "ready"}
            onPress={send}
            style={({ pressed }) => [
              styles.send,
              draft.trim().length === 0 && { opacity: 0.4 },
              pressed && { opacity: 0.75 },
            ]}
            testID="chat-send"
          >
            <Icon name="send" strokeWidth={2.4} />
          </Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

function Bubble({ message }: { message: Message }) {
  const mine = message.author === "client";
  return (
    <View style={{ alignItems: mine ? "flex-end" : "flex-start", gap: 4 }}>
      {mine ? null : (
        <Txt size={12} weight="semibold" color={colors.muted}>
          {message.authorName ? `${message.authorName} from Finvest` : "Finvest"}
        </Txt>
      )}
      <View
        style={[styles.bubble, mine ? styles.mine : styles.theirs]}
        accessibilityLabel={`${mine ? "You" : (message.authorName ?? "Finvest")}, ${clockTime(message.createdAt)}: ${message.body}`}
      >
        <Txt size={14} lineHeight={20} color={mine ? colors.white : colors.ink}>
          {message.body}
        </Txt>
      </View>
    </View>
  );
}

// React Native Web draws its own focus ring. The border already shows focus.

const styles = StyleSheet.create({
  page: { flex: 1, paddingHorizontal: 20, paddingBottom: 16, gap: 12 },
  wide: { paddingTop: 28, paddingHorizontal: 32, maxWidth: 720, width: "100%" },
  header: { flexDirection: "row", alignItems: "center", gap: 12 },
  pill: {
    height: 44,
    paddingHorizontal: 14,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    justifyContent: "center",
  },
  messages: { flexGrow: 1, gap: 12, paddingVertical: 4 },
  intro: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 16,
    gap: 4,
  },
  bubble: { maxWidth: 290, paddingVertical: 12, paddingHorizontal: 14, borderRadius: 20 },
  mine: { backgroundColor: colors.black, borderBottomRightRadius: 6 },
  theirs: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderBottomLeftRadius: 6,
  },
  composer: { flexDirection: "row", alignItems: "center", gap: 10 },
  input: {
    flex: 1,
    height: 52,
    borderRadius: 26,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    paddingHorizontal: 18,
    fontFamily: fontFor("medium"),
    fontSize: 15,
    color: colors.ink,
  },
  send: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
  },
});
