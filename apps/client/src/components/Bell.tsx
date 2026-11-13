import { useCallback } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";

import { useSupport } from "@/support/SupportProvider";
import { colors } from "@/theme";

import { Icon } from "./Icon";
import { Txt } from "./Txt";

/** Opens the notification centre. Shows how many are unread, read again each time the page is shown. */
export function Bell() {
  const router = useRouter();
  const { unread, refreshUnread } = useSupport();

  useFocusEffect(
    useCallback(() => {
      void refreshUnread();
    }, [refreshUnread]),
  );

  const count = unread ?? 0;
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={count > 0 ? `Notifications, ${count} unread` : "Notifications"}
      onPress={() => router.push("/notifications")}
      style={({ pressed }) => [styles.button, pressed && { opacity: 0.7 }]}
      testID="open-notifications"
    >
      <Icon name="bell" />
      {count > 0 ? (
        <View style={styles.badge}>
          <Txt size={11} weight="bold" lineHeight={14} color={colors.ink}>
            {count > 9 ? "9+" : String(count)}
          </Txt>
        </View>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  badge: {
    position: "absolute",
    top: -4,
    right: -4,
    minWidth: 20,
    height: 20,
    paddingHorizontal: 5,
    borderRadius: 10,
    backgroundColor: colors.lime,
    borderWidth: 1,
    borderColor: colors.limeBorder,
    alignItems: "center",
    justifyContent: "center",
  },
});
