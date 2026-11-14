import { ReactNode, useCallback, useRef, useState } from "react";
import { StyleSheet, View } from "react-native";

import { useAuth } from "@/auth/AuthProvider";
import { PIN_LENGTH } from "@/auth/pin";
import { AuthError, describeAuthError, PinAction } from "@/auth/types";
import { colors, fontSizes } from "@/theme";

import { TextLink } from "../Button";
import { Icon } from "../Icon";
import { Screen } from "../Screen";
import { Title, Txt } from "../Txt";
import { PinDots, PinPad } from "./PinPad";

/** What the client is confirming, in their words. */
const WHAT: Record<PinAction, string> = {
  withdrawal: "Enter your PIN to confirm this withdrawal",
  payout_account: "Enter your PIN to add this payout account",
};

/**
 * What asking said: a confirmation to send with the action, none when this phone has no PIN
 * (a browser never has one), or `cancelled` when the client went back.
 */
export type PinAnswer = { confirmation: string | null } | "cancelled";

/**
 * Confirm with your PIN, from the design canvas. On the phone app with a PIN, `ask` swaps the
 * screen for the PIN pad and resolves with a confirmation for that one action. Without a PIN it
 * resolves at once with none, and the API asks for nothing. Render `screen` in place of the
 * screen's own content while it is set, so the form behind it keeps what was typed.
 */
export function usePinConfirmation(): { ask: (action: PinAction) => Promise<PinAnswer>; screen: ReactNode } {
  const { session, pinApplies } = useAuth();
  const [action, setAction] = useState<PinAction | null>(null);
  const resolver = useRef<((answer: PinAnswer) => void) | null>(null);
  const needed = pinApplies && Boolean(session?.pinSet);

  const ask = useCallback(
    (next: PinAction): Promise<PinAnswer> => {
      if (!needed) return Promise.resolve({ confirmation: null });
      return new Promise<PinAnswer>((resolve) => {
        resolver.current = resolve;
        setAction(next);
      });
    },
    [needed],
  );

  const finish = (answer: PinAnswer) => {
    resolver.current?.(answer);
    resolver.current = null;
    setAction(null);
  };

  return {
    ask,
    screen: action ? <ConfirmWithPin action={action} onDone={finish} /> : null,
  };
}

function ConfirmWithPin({ action, onDone }: { action: PinAction; onDone: (answer: PinAnswer) => void }) {
  const { client, setSession, setLocked, setSignedOutForPins } = useAuth();
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = async (tried: string) => {
    setBusy(true);
    try {
      const checked = await client.confirmWithPin(tried, action);
      if (checked.confirmed && checked.confirmation) {
        onDone({ confirmation: checked.confirmation });
        return;
      }
      setPin("");
      setError(
        checked.triesLeft === 1
          ? "That PIN is not right. 1 try left before this phone is signed out."
          : `That PIN is not right. ${checked.triesLeft} tries left before this phone is signed out.`,
      );
    } catch (caught) {
      setPin("");
      if (caught instanceof AuthError && caught.code === "signed_out") {
        onDone("cancelled");
        setSignedOutForPins(true);
        setLocked(false);
        setSession(null);
      } else {
        setError(describeAuthError(caught));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen
      dark
      signedIn
      footer={<TextLink label="Cancel" onDark onPress={() => onDone("cancelled")} testID="pin-confirm-cancel" />}
    >
      <View style={styles.center}>
        <View style={styles.badge}>
          <Icon name="shield" size={30} color={colors.black} />
        </View>
        <Title onDark center>
          Confirm with your PIN
        </Title>
        <Txt color={colors.onBlackMuted} center>
          {WHAT[action]}
        </Txt>
        <View style={{ marginTop: 14 }}>
          <PinDots count={pin.length} invalid={Boolean(error)} />
        </View>
        <View style={{ minHeight: 44, justifyContent: "center" }} accessibilityLiveRegion="polite">
          {error ? (
            <Txt size={fontSizes.sm} weight="semibold" color={colors.lime} center testID="pin-confirm-error">
              {error}
            </Txt>
          ) : null}
        </View>
      </View>
      <PinPad
        value={pin}
        onChange={(next) => {
          setPin(next);
          setError(null);
          if (next.length === PIN_LENGTH) void check(next);
        }}
        disabled={busy}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  center: { marginTop: 24, alignItems: "center", gap: 12 },
  badge: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 4,
  },
});
