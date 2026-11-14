import { useEffect, useState } from "react";
import { ActivityIndicator, Platform, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { useLinkToken } from "@/auth/emailLink";
import { checkPassword, passwordIsAcceptable } from "@/auth/password";
import { AuthError, ClaimChallenge, describeAuthError } from "@/auth/types";
import { Card, CheckRow, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import { PasswordField } from "@/components/Field";
import { Logo } from "@/components/Logo";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

/** What a client sees for a link that is used, replaced by a newer one, or too old. */
export const LINK_GONE =
  "This link no longer works. It may have been used already, or we sent you a newer one. Open the newest link we sent you by SMS or email.";

function linkError(caught: unknown): string {
  return caught instanceof AuthError && caught.code === "not_found" ? LINK_GONE : describeAuthError(caught);
}

function Top() {
  return (
    <View style={{ marginTop: 12 }}>
      <Logo />
    </View>
  );
}

/** Step 1: what this is, and a code to the number Finvest 1.0 had. */
function Start({ token, onSent }: { token: string; onSent: (challenge: ClaimChallenge) => void }) {
  const router = useRouter();
  const { client } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      onSent(await client.startClaim(token));
    } catch (caught) {
      setError(linkError(caught));
      setBusy(false);
    }
  };

  return (
    <Screen footer={<Button label="Text me a code" onPress={send} loading={busy} testID="claim-start" />}>
      <Stack.Screen options={{ title: "Your account has moved" }} />
      <Top />
      <View style={{ marginTop: 32, gap: 10 }}>
        <Title>Your account has moved to Finvest 2.0</Title>
        <Txt color={colors.muted}>
          We carried your Finvest 1.0 investments over. To open your account, we will text a 6 digit
          code to the mobile number we have for you, and you will choose a new password.
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 14 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <Card>
          <View style={{ gap: 12 }}>
            <Txt weight="bold">What happens next</Txt>
            <Txt size={fontSizes.sm} color={colors.muted}>
              1. Enter the code and choose a password.
            </Txt>
            <Txt size={fontSizes.sm} color={colors.muted}>
              2. Confirm it is you, with a selfie or an ID check.
            </Txt>
            <Txt size={fontSizes.sm} color={colors.muted}>
              3. Check the investments we carried over, and tell us if anything is wrong.
            </Txt>
          </View>
        </Card>
        <TextLink label="I no longer have that number" onPress={() => router.push("/recover")} />
      </View>
    </Screen>
  );
}

/** Step 2: the texted code and a new password together, as the server checks them together. */
function Finish({
  token,
  challenge,
  onChallenge,
}: {
  token: string;
  challenge: ClaimChallenge;
  onChallenge: (challenge: ClaimChallenge) => void;
}) {
  const router = useRouter();
  const { client, setSession, pinApplies } = useAuth();
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [codeWrong, setCodeWrong] = useState(false);
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);

  // The number on file is not shown before the code proves who is holding the phone, so the
  // password is not checked against it here. The server checks it.
  const checks = checkPassword(password, confirmation, "");
  const ready = code.length === CODE_LENGTH && passwordIsAcceptable(checks);

  const resend = async () => {
    setResending(true);
    setError(null);
    setResent(false);
    try {
      onChallenge(await client.startClaim(token));
      setCode("");
      setCodeWrong(false);
      setResent(true);
    } catch (caught) {
      setError(linkError(caught));
    } finally {
      setResending(false);
    }
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    setCodeWrong(false);
    setResent(false);
    try {
      const session = await client.completeClaim(token, challenge.challengeId, code, password);
      setSession(session);
      // A phone chooses its PIN first, and Home then leads on to the next step.
      router.replace(pinApplies ? "/" : "/moving");
    } catch (caught) {
      setError(linkError(caught));
      if (caught instanceof AuthError && caught.code === "invalid_code") {
        setCodeWrong(true);
        setCode("");
      }
      setBusy(false);
    }
  };

  return (
    <Screen
      footer={
        <Button label="Open my account" onPress={submit} loading={busy} disabled={!ready} testID="claim-complete" />
      }
    >
      <Stack.Screen options={{ title: "Choose a password" }} />
      <Top />
      <View style={{ marginTop: 32, gap: 10 }}>
        <Title>Enter the code and choose a password</Title>
        <Txt color={colors.muted}>
          We texted a 6 digit code to the mobile number Finvest 1.0 had for you.
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 14 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        {resent ? <Notice tone="neutral">We sent a new code. Only the newest one works.</Notice> : null}
        <View style={{ gap: 8 }}>
          <Eyebrow>Code from the SMS</Eyebrow>
          <CodeInput
            value={code}
            onChangeText={(next) => {
              setCode(next);
              setCodeWrong(false);
            }}
            invalid={codeWrong}
          />
          <TextLink
            label={resending ? "Sending…" : "Send the code again"}
            onPress={() => (resending ? undefined : resend())}
            testID="claim-resend"
          />
        </View>
        <PasswordField
          isNew
          label="New password"
          value={password}
          onChangeText={(next) => {
            setPassword(next);
            setError(null);
          }}
          testID="password-input"
        />
        <PasswordField
          isNew
          label="Type it again"
          value={confirmation}
          onChangeText={setConfirmation}
          testID="password-confirm-input"
        />
        <Card padded={false}>
          <View style={{ paddingVertical: 10, paddingHorizontal: 16 }}>
            <CheckRow met={checks.longEnough}>At least 10 characters</CheckRow>
            <CheckRow met={checks.matches}>Matches in both boxes</CheckRow>
          </View>
        </Card>
        <Txt size={fontSizes.sm} color={colors.muted}>
          Your Finvest 1.0 password does not carry over. We never ask for your password or this code
          by phone, SMS or WhatsApp. If someone does, do not share it.
        </Txt>
      </View>
    </Screen>
  );
}

/** Someone is already signed in on this device. The link is for one account only. */
function SignedInAlready() {
  const { client, setSession } = useAuth();
  const [busy, setBusy] = useState(false);
  const signOut = async () => {
    setBusy(true);
    await client.signOut().catch(() => undefined);
    setSession(null);
    setBusy(false);
  };
  return (
    <Screen footer={<Button label="Log out and continue" onPress={signOut} loading={busy} testID="claim-sign-out" />}>
      <Stack.Screen options={{ title: "Your account has moved" }} />
      <Top />
      <View style={{ marginTop: 32, gap: 10 }}>
        <Title>Log out first</Title>
        <Txt color={colors.muted}>
          Someone is logged in to Finvest on this device. To open the account this link is for, log
          out first. The link keeps working.
        </Txt>
      </View>
    </Screen>
  );
}

/**
 * The page the link to a moved Finvest 1.0 account opens:
 * https://app.finvestug.com/claim#<token>. Not signed in: the code and the new
 * password make the account the client's and open their first session.
 */
export default function Claim() {
  const router = useRouter();
  const { session } = useAuth();
  const token = useLinkToken();
  const [challenge, setChallenge] = useState<ClaimChallenge | null>(null);

  useEffect(() => {
    if (Platform.OS === "web" && typeof window !== "undefined" && window.location.hash) {
      // Keep the token out of the address bar, the history and any screenshot.
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  if (session) return <SignedInAlready />;
  if (token === undefined) {
    return (
      <Screen>
        <ActivityIndicator color={colors.ink} style={{ marginTop: 80 }} />
      </Screen>
    );
  }
  if (token === null) {
    return (
      <Screen>
        <Stack.Screen options={{ title: "Your account has moved" }} />
        <Top />
        <View style={{ marginTop: 32, gap: 14 }}>
          <Title>Open the link from our message</Title>
          <Notice tone="danger">{LINK_GONE}</Notice>
          <TextLink label="Ask us to call you" onPress={() => router.push("/recover")} />
        </View>
      </Screen>
    );
  }
  if (!challenge) return <Start token={token} onSent={setChallenge} />;
  return <Finish token={token} challenge={challenge} onChallenge={setChallenge} />;
}
