import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors } from "@/theme";

import { beingChecked, IdentityCheck } from "./types";
import { useIdentityCheck } from "./useIdentityCheck";

function copyFor(check: IdentityCheck | null): { title: string; detail: string } | null {
  if (!check) return { title: "Confirm who you are", detail: "Needed before you invest. It takes about two minutes." };
  if (check.status === "draft") return { title: "Finish your ID check", detail: "Pick up where you left off. What you did is saved." };
  if (check.status === "awaiting_client") {
    return {
      title: check.retakePhotos.length > 1 ? "We need new photos" : "We need a new photo",
      detail: "Take it again to finish your ID check.",
    };
  }
  if (beingChecked(check.status)) return { title: "We are checking your ID", detail: "We will tell you as soon as it is done." };
  if (check.status === "rejected") return { title: "Your ID check", detail: "We could not confirm your identity. See why." };
  return null;
}

/**
 * On Home until the client is approved: what their ID check needs next. Quiet
 * while it loads or if it cannot be read, and gone once approved.
 */
export function IdCheckCard() {
  const router = useRouter();
  const state = useIdentityCheck();
  if (state.status !== "ready") return null;
  const copy = copyFor(state.check);
  if (!copy) return null;
  const urgent = !state.check || state.check.status === "draft" || state.check.status === "awaiting_client";
  return (
    <Pressable
      accessibilityRole="link"
      onPress={() => router.push("/id-check")}
      style={({ pressed }) => [styles.card, urgent && styles.urgent, pressed && { opacity: 0.7 }]}
      testID="home-id-check"
    >
      <View style={styles.mark}>
        <Icon name="shield" color={colors.lime} />
      </View>
      <View style={{ flex: 1 }}>
        <Txt weight="bold" color={urgent ? colors.white : colors.ink}>
          {copy.title}
        </Txt>
        <Txt size={13} color={urgent ? colors.onBlackMuted : colors.muted}>
          {copy.detail}
        </Txt>
      </View>
      <Icon name="chevron" size={16} strokeWidth={2.2} color={urgent ? colors.white : colors.ink} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 14,
  },
  urgent: { backgroundColor: colors.black, borderColor: colors.black },
  mark: {
    width: 44,
    height: 44,
    borderRadius: 14,
    backgroundColor: colors.onBlackSurface,
    alignItems: "center",
    justifyContent: "center",
  },
});
