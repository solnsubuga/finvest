import { useCallback, useState } from "react";
import { View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164, toE164 } from "@/auth/phone";
import { AuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import { ConfirmItIsYou, useConfirmedChange } from "@/components/ConfirmItIsYou";
import { Done } from "@/components/Done";
import { PhoneField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

const SUPPORT = "Or ask us in Help to change your number.";

/**
 * Sign in and security: move the account to a new mobile number. A passkey or the authenticator
 * app first, then a code texted to the new number proves the client holds it.
 */
export default function ChangePhone() {
  const router = useRouter();
  const { client, session, setSession } = useAuth();
  const [phone, setPhone] = useState("");
  const [challenge, setChallenge] = useState<{ id: string; phone: string } | null>(null);
  const [code, setCode] = useState("");
  const [codeWrong, setCodeWrong] = useState(false);
  const [moved, setMoved] = useState<string | null>(null);

  const target = toE164(phone);
  const start = useCallback(() => client.startPhoneChange(target ?? phone), [client, target, phone]);
  const onStarted = useCallback((id: string) => setChallenge({ id, phone: target ?? phone }), [target, phone]);
  const sending = useConfirmedChange(start, onStarted);

  const finish = useCallback(async () => {
    if (!challenge) throw new AuthError("expired");
    try {
      return await client.confirmPhoneChange(challenge.id, code);
    } catch (caught) {
      if (caught instanceof AuthError && caught.code === "invalid_code") {
        setCodeWrong(true);
        setCode("");
      }
      throw caught;
    }
  }, [client, challenge, code]);
  const onMoved = useCallback(
    (next: string) => {
      setMoved(next);
      if (session) setSession({ ...session, phone: next, restricted: true });
    },
    [session, setSession],
  );
  const confirming = useConfirmedChange(finish, onMoved);

  if (!session) return null;
  if (moved) {
    return (
      <Done
        title="Number changed"
        detail={`Your account is now on ${formatE164(moved)}. We signed out your other devices and told your old number. For the next 24 hours withdrawals go only to accounts you have used before.`}
        action="Back to security"
        onPress={() => router.dismissTo("/security")}
      />
    );
  }

  const step = challenge ? confirming : sending;
  const error = step.error;

  return (
    <Screen
      signedIn
      footer={
        step.confirming ? null : challenge ? (
          <Button
            label="Change my number"
            onPress={confirming.run}
            loading={confirming.busy}
            disabled={code.length !== CODE_LENGTH}
            testID="phone-confirm"
          />
        ) : (
          <Button label="Send a code" onPress={sending.run} loading={sending.busy} disabled={!target} testID="phone-send" />
        )
      }
    >
      <Stack.Screen options={{ title: "Change mobile number" }} />
      <BackButton fallback="/security" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Sign in and security</Eyebrow>
        <Title>Change your mobile number</Title>
        <Txt color={colors.muted}>
          {challenge
            ? `We texted a 6 digit code to ${formatE164(challenge.phone)}. It works for 10 minutes.`
            : `Now ${formatE164(session.phone)}. Your number is how you log in and get codes.`}
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 14 }}>
        {step.confirming ? (
          <ConfirmItIsYou onConfirmed={step.run} onCancel={() => step.setConfirming(false)} otherwise={SUPPORT} />
        ) : null}
        {error ? <Notice tone="danger">{error}</Notice> : null}
        {challenge ? (
          <View style={{ gap: 8 }}>
            <Eyebrow>Code from the SMS</Eyebrow>
            <CodeInput
              value={code}
              onChangeText={(next) => {
                setCode(next);
                setCodeWrong(false);
                confirming.setError(null);
              }}
              invalid={codeWrong}
            />
            <View style={{ alignSelf: "flex-start" }}>
              <TextLink
                label="Use a different number"
                onPress={() => {
                  setChallenge(null);
                  setCode("");
                }}
              />
            </View>
          </View>
        ) : (
          <>
            <PhoneField label="New mobile number" value={phone} onChangeText={setPhone} testID="phone-new" autoFocus />
            <Notice tone="warning">
              You confirm it is you with your passkey or authenticator app first. When the number changes we sign
              out your other devices, and for 24 hours withdrawals go only to accounts you have used before.
            </Notice>
          </>
        )}
      </View>
    </Screen>
  );
}
