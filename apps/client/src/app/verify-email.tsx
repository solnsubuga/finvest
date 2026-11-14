import { useEffect, useState } from "react";
import { ActivityIndicator, Platform, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { useLinkToken } from "@/auth/emailLink";
import { AuthError, describeAuthError } from "@/auth/types";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Done } from "@/components/Done";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

type State =
  | { status: "checking" }
  | { status: "proved" }
  | { status: "expired" }
  | { status: "failed"; message: string };

/** Proving an email address: the screen the link in our email opens, signed in or not. */
export default function VerifyEmail() {
  const router = useRouter();
  const { client, session, setSession } = useAuth();
  const token = useLinkToken();
  const [checked, setState] = useState<State>({ status: "checking" });
  // A link with no token is as good as an expired one, and nothing is sent.
  const state: State = token === null ? { status: "expired" } : checked;
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (Platform.OS === "web" && typeof window !== "undefined" && window.location.hash) {
      // Keep the token out of the address bar, the history and any screenshot.
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  useEffect(() => {
    if (!token) {
      return undefined;
    }
    let live = true;
    client
      .confirmEmail(token)
      .then(async () => {
        // The signed-in session learns the address is proved.
        const fresh = await client.restoreSession().catch(() => null);
        if (fresh) setSession(fresh);
        if (live) setState({ status: "proved" });
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setState(
          caught instanceof AuthError && caught.code === "expired"
            ? { status: "expired" }
            : { status: "failed", message: describeAuthError(caught) },
        );
      });
    return () => {
      live = false;
    };
    // `attempt` is here so "Try again" sends the same token once more.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, token, attempt]);

  const onward = () => router.replace(session ? "/profile" : "/welcome");

  if (state.status === "proved") {
    return (
      <>
        <Stack.Screen options={{ title: "Email address proved" }} />
        <Done
          title="Your email address is proved"
          detail="Statements and receipts will be emailed to it from now on."
          action={session ? "Back to Profile" : "Open Finvest"}
          onPress={onward}
        />
      </>
    );
  }

  return (
    <Screen
      footer={
        state.status === "checking" ? undefined : state.status === "failed" ? (
          <Button label="Try again" onPress={() => {
              setState({ status: "checking" });
              setAttempt((value) => value + 1);
            }}
            testID="verify-retry" />
        ) : (
          <Button label={session ? "Go to Profile" : "Log in"} onPress={() => router.replace(session ? "/profile" : "/login")} testID="verify-onward" />
        )
      }
    >
      <Stack.Screen options={{ title: "Prove your email address" }} />
      {state.status === "checking" ? (
        <View style={{ marginTop: 80, gap: 16, alignItems: "flex-start" }} accessibilityLabel="Checking the link">
          <ActivityIndicator color={colors.black} />
          <Txt color={colors.muted}>Checking your link…</Txt>
        </View>
      ) : null}
      {state.status === "expired" ? (
        <View style={{ marginTop: 40, gap: 12 }}>
          <Title>This link no longer works</Title>
          <Txt color={colors.muted}>
            Links work once, for 24 hours, and only the newest one we sent. Log in and send a new
            one from Profile.
          </Txt>
        </View>
      ) : null}
      {state.status === "failed" ? (
        <View style={{ marginTop: 40, gap: 12 }}>
          <Title>We could not check your link</Title>
          <Notice tone="danger">{state.message}</Notice>
        </View>
      ) : null}
    </Screen>
  );
}
