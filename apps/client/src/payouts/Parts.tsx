import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useFocusEffect } from "expo-router";

import { Txt } from "@/components/Txt";
import { formatMoney } from "@/payments/format";
import { formatDay } from "@/investing/format";
import { colors, fontSizes } from "@/theme";

import { accountLabel, accountProvider, accountStatusPill, payoutCopy, Tone } from "./format";
import { describePayoutsError, Payout, PayoutAccount } from "./types";

const toneColors: Record<Tone, { background: string; text: string }> = {
  lime: { background: colors.limeSurface, text: colors.limeText },
  warning: { background: colors.warningSurface, text: colors.warningText },
  danger: { background: colors.dangerSurface, text: colors.danger },
  neutral: { background: colors.surfaceSunken, text: colors.ink },
};

export function Pill({ label, tone }: { label: string; tone: Tone }) {
  const palette = toneColors[tone];
  return (
    <View style={[styles.pill, { backgroundColor: palette.background }]}>
      <Txt size={12} weight="bold" color={palette.text}>
        {label}
      </Txt>
    </View>
  );
}

/** One account in the list: which provider, the masked number, and where it is. */
export function AccountCard({ account, nowMs, onPress }: { account: PayoutAccount; nowMs: number; onPress: () => void }) {
  const pill = accountStatusPill(account, nowMs);
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={`${accountLabel(account)}. ${pill.label}`}
      onPress={onPress}
      testID={`payout-account-${account.id}`}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.75 }]}
    >
      <View style={styles.top}>
        <Txt size={fontSizes.md} weight="extrabold" style={{ flex: 1 }}>
          {accountProvider(account)}
        </Txt>
        <Pill {...pill} />
      </View>
      <Txt size={14} weight="semibold">
        {account.account}
      </Txt>
      <Txt size={13} color={colors.muted}>
        {account.holderName}
      </Txt>
    </Pressable>
  );
}

/** One payout in the list: how much, where to, and how it stands. */
export function PayoutCard({ payout, account, onPress }: { payout: Payout; account: PayoutAccount | undefined; onPress: () => void }) {
  const copy = payoutCopy(payout);
  const where = account ? accountLabel(account) : payout.kind === "bank" ? "A bank account" : "Mobile money";
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={`${formatMoney(payout.amount)} to ${where}. ${copy.label}`}
      onPress={onPress}
      testID={`payout-${payout.id}`}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.75 }]}
    >
      <View style={styles.top}>
        <Txt size={20} weight="extrabold" style={{ letterSpacing: -0.4 }}>
          {formatMoney(payout.amount)}
        </Txt>
        <Pill label={copy.label} tone={copy.tone} />
      </View>
      <Txt size={14} weight="semibold">
        {`To ${where}`}
      </Txt>
      <Txt size={13} color={colors.muted}>
        {`Asked for on ${formatDay(payout.businessDate)}`}
      </Txt>
    </Pressable>
  );
}

/** `readAtMs` is when the answer came, the moment a "can it be paid yet" is judged at. */
export type Loaded<T> =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; value: T; readAtMs: number };

/** Reads when the screen comes into view, so a screen shows what changed while it was away. */
export function useFocusLoaded<T>(load: () => Promise<T>): [Loaded<T>, () => void, (value: T) => void] {
  const [loaded, setLoaded] = useState<Loaded<T>>({ state: "loading" });
  const run = useCallback(() => {
    let live = true;
    load().then(
      (value) => live && setLoaded({ state: "ready", value, readAtMs: Date.now() }),
      (error: unknown) => live && setLoaded({ state: "failed", message: describePayoutsError(error) }),
    );
    return () => {
      live = false;
    };
  }, [load]);
  useFocusEffect(run);
  const retry = useCallback(() => {
    setLoaded({ state: "loading" });
    run();
  }, [run]);
  const replace = useCallback((value: T) => setLoaded({ state: "ready", value, readAtMs: Date.now() }), []);
  return [loaded, retry, replace];
}

const styles = StyleSheet.create({
  pill: { borderRadius: 12, paddingHorizontal: 10, paddingVertical: 4 },
  card: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 16,
    gap: 4,
  },
  top: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 12 },
});
