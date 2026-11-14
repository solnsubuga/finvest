import { StyleSheet, View } from "react-native";

import { Notice } from "@/components/Blocks";
import { TextLink } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors } from "@/theme";

import { useEmailProof } from "./useEmailProof";

/**
 * Home's reminder to confirm the email address, shown until the client opens the link. It never
 * stops them using the app.
 */
export function EmailProofBanner() {
  const { email, pending, resend, sending, note } = useEmailProof();
  if (!pending) {
    return null;
  }
  return (
    <View style={styles.banner} testID="email-proof-banner">
      <View style={styles.row}>
        <View style={styles.icon}>
          <Icon name="mail" color={colors.lime} />
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <Txt weight="bold">Confirm your email</Txt>
          <Txt size={13} color={colors.muted}>
            Open the link we sent to {email}. Then we can email your statements and receipts, and you
            can log in with your email.
          </Txt>
        </View>
      </View>
      {note ? <Notice tone={note.tone}>{note.text}</Notice> : null}
      <View style={styles.actions}>
        <TextLink
          label={sending ? "Sending…" : "Send another link"}
          onPress={sending ? () => {} : () => void resend()}
          testID="email-proof-resend"
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    gap: 10,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 18,
    padding: 14,
  },
  row: { flexDirection: "row", alignItems: "flex-start", gap: 12 },
  icon: {
    width: 40,
    height: 40,
    borderRadius: 14,
    backgroundColor: colors.black,
    alignItems: "center",
    justifyContent: "center",
  },
  actions: { flexDirection: "row", paddingLeft: 52 },
});

