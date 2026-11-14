import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors } from "@/theme";

import { Claim } from "./types";

function copyFor(claim: Claim): { title: string; detail: string; urgent: boolean } | null {
  switch (claim.status) {
    case "signed_in":
      return { title: "Confirm who you are", detail: "An ID check, to finish opening your account.", urgent: true };
    case "identity_submitted":
      return { title: "We are checking it is you", detail: "We will tell you as soon as it is done.", urgent: false };
    case "identity_confirmed":
      return { title: "Check your investments", detail: "Confirm the ones we copied from your paper records.", urgent: true };
    case "problem_reported":
      return { title: "We are looking into it", detail: "Staff will contact you about what you told us.", urgent: false };
    default:
      return null;
  }
}

/** On Home until a client moved from paper records has confirmed their investments. */
export function ClaimCard({ claim }: { claim: Claim }) {
  const router = useRouter();
  const copy = copyFor(claim);
  if (!copy) return null;
  const { urgent } = copy;
  return (
    <Pressable
      accessibilityRole="link"
      onPress={() => router.push("/moving")}
      style={({ pressed }) => [styles.card, urgent && styles.urgent, pressed && { opacity: 0.7 }]}
      testID="home-claim"
    >
      <View style={styles.mark}>
        <Icon name={claim.status === "identity_confirmed" ? "portfolio" : "shield"} color={colors.lime} />
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
