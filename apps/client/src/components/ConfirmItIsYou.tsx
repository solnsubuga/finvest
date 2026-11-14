import { useCallback, useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { AuthError, describeAuthError } from "@/auth/types";
import { useSecondSteps } from "@/auth/useSecondSteps";
import { colors, fontSizes } from "@/theme";

import { Card, Notice } from "./Blocks";
import { Button, TextLink } from "./Button";
import { CODE_LENGTH, CodeInput } from "./CodeInput";
import { Icon } from "./Icon";
import { Eyebrow, Txt } from "./Txt";

/** True when the server wants a passkey or authenticator app before this change. */
export function needsConfirmation(error: unknown): boolean {
  return error instanceof AuthError && error.code === "confirm_it_is_you";
}

/**
 * Runs a change to how the client signs in. When the server first wants a passkey or the
 * authenticator app, `confirming` turns on; once the client confirms, the same change is sent
 * again by itself.
 */
export function useConfirmedChange<T>(change: () => Promise<T>, onDone: (result: T) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await change();
      setConfirming(false);
      onDone(result);
    } catch (caught) {
      if (needsConfirmation(caught)) {
        setConfirming(true);
      } else {
        setError(describeAuthError(caught));
      }
    } finally {
      setBusy(false);
    }
  }, [change, onDone]);

  return { run, busy, error, setError, confirming, setConfirming };
}

type Props = {
  /** Called once the client has confirmed. The change is then sent again. */
  onConfirmed: () => void;
  onCancel: () => void;
  /** What the client can do instead when they have no passkey or app, such as asking support. */
  otherwise?: string;
};

/**
 * Confirm it is you: a passkey or a code from the authenticator app, never a texted code, because
 * whoever holds the SIM could have that. A client with neither is sent to set one up.
 */
export function ConfirmItIsYou({ onConfirmed, onCancel, otherwise }: Props) {
  const router = useRouter();
  const { client } = useAuth();
  const steps = useSecondSteps();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wrong, setWrong] = useState(false);

  const methods = steps.status === "ready" ? steps.methods : [];
  const hasPasskey = methods.includes("passkey");
  const hasApp = methods.includes("authenticator");

  const confirm = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setWrong(false);
    try {
      await work();
      onConfirmed();
    } catch (caught) {
      setError(describeAuthError(caught));
      if (caught instanceof AuthError && caught.code === "invalid_code") {
        setWrong(true);
        setCode("");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <View style={{ gap: 12 }} testID="confirm-it-is-you">
        <View style={{ flexDirection: "row", gap: 10, alignItems: "center" }}>
          <Icon name="shield" size={22} />
          <Txt weight="bold">Confirm it is you</Txt>
        </View>
        {steps.status === "ready" && !hasPasskey && !hasApp ? (
          <>
            <Txt size={fontSizes.sm} color={colors.muted}>
              {`For your safety, this needs a passkey or an authenticator app, not an SMS code. Set one up first, then come back.${otherwise ? ` ${otherwise}` : ""}`}
            </Txt>
            <Button
              label="Set up a passkey or app"
              height={52}
              onPress={() => router.navigate("/security")}
              testID="confirm-set-up"
            />
          </>
        ) : (
          <>
            <Txt size={fontSizes.sm} color={colors.muted}>
              For your safety, use your passkey or authenticator app. An SMS code is not enough for
              this change.
            </Txt>
            {error ? <Notice tone="danger">{error}</Notice> : null}
            {hasPasskey ? (
              <Button
                label="Use my passkey"
                height={52}
                loading={busy && !code}
                onPress={() => void confirm(() => client.confirmWithPasskey())}
                testID="confirm-passkey"
              />
            ) : null}
            {hasApp ? (
              <View style={{ gap: 8 }}>
                <Eyebrow>Code from your authenticator app</Eyebrow>
                <CodeInput
                  value={code}
                  onChangeText={(next) => {
                    setCode(next);
                    setWrong(false);
                  }}
                  invalid={wrong}
                />
                <Button
                  label="Confirm with the code"
                  variant={hasPasskey ? "secondary" : "primary"}
                  height={52}
                  loading={busy && Boolean(code)}
                  disabled={code.length !== CODE_LENGTH}
                  onPress={() => void confirm(() => client.confirmWithAuthenticator(code))}
                  testID="confirm-app"
                />
              </View>
            ) : null}
          </>
        )}
        <View style={{ alignSelf: "center" }}>
          <TextLink label="Not now" onPress={onCancel} />
        </View>
      </View>
    </Card>
  );
}
