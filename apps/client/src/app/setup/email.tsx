import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { signupSteps, stepOf } from "@/auth/signupSteps";
import { useEmailProof } from "@/auth/useEmailProof";
import { Notice, StepProgress } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

/**
 * Straight after the password: sign up has emailed a link to the address the client gave. They
 * may open it now or later. It never holds them up, and Home keeps reminding them until it is done.
 */
export default function CheckEmail() {
  const router = useRouter();
  const { session, pinApplies, setJustSignedUp } = useAuth();
  const { email, proved, resend, sending, note } = useEmailProof();

  if (!session) {
    return <Redirect href="/welcome" />;
  }

  // The next sign-up step on this device: the PIN on the phone app, then the offer of a passkey.
  const next = () => {
    if (pinApplies && !session.pinSet) {
      router.replace("/setup/pin");
    } else if (signupSteps(pinApplies).includes("passkey")) {
      router.replace("/setup/passkey");
    } else {
      setJustSignedUp(false);
      router.replace("/");
    }
  };

  return (
    <Screen
      signedIn
      footer={
        proved ? (
          <Button label="Continue" onPress={next} testID="email-step-continue" />
        ) : (
          <>
            <Button label="Continue" onPress={next} testID="email-step-continue" />
            <TextLink label="I'll do this later" onPress={next} testID="email-step-later" />
          </>
        )
      }
    >
      <Stack.Screen options={{ title: proved ? "Email confirmed" : "Check your email" }} />
      <StepProgress {...stepOf("email", pinApplies)} />
      <View style={styles.badge}>
        <Icon name={proved ? "check" : "mail"} size={34} color={colors.lime} />
      </View>
      <View style={{ marginTop: 20, gap: 10 }}>
        <Title>{proved ? "Email confirmed" : "Check your email"}</Title>
        {proved ? (
          <Txt color={colors.muted}>
            {email} is yours. We will email your statements and receipts there, and you can log in
            with it.
          </Txt>
        ) : (
          <Txt color={colors.muted}>
            We sent a link to <Txt weight="bold">{email}</Txt>. Open it on any device within 24 hours
            to confirm the address is yours.
          </Txt>
        )}
      </View>

      {!proved ? (
        <View style={{ marginTop: 20, gap: 12 }}>
          {note ? <Notice tone={note.tone}>{note.text}</Notice> : null}
          <View style={styles.note}>
            <Txt size={fontSizes.sm} lineHeight={19}>
              <Txt size={fontSizes.sm} weight="bold" lineHeight={19}>
                You can start using Finvest now.
              </Txt>{" "}
              Until you confirm, we keep your statements and receipts in the app instead of emailing
              them, and you log in with your number.
            </Txt>
          </View>
          <View style={{ flexDirection: "row" }}>
            <TextLink
              label={sending ? "Sending…" : "Send another link"}
              onPress={sending ? () => {} : () => void resend()}
              testID="email-step-resend"
            />
          </View>
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  badge: {
    marginTop: 26,
    width: 72,
    height: 72,
    borderRadius: 24,
    backgroundColor: colors.black,
    alignItems: "center",
    justifyContent: "center",
  },
  note: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 18,
    paddingVertical: 14,
    paddingHorizontal: 16,
  },
});
