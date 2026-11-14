import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { AccountPicker, firstChoosable, NoAccount } from "@/investing/AccountPicker";
import { atMaturityDetail, atMaturityLabel, formatAmount, formatDay } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { AtMaturity, describeInvestingError, Investment, InvestingError, PayoutAccount } from "@/investing/types";
import { useLoaded } from "@/investing/useLoaded";
import { Loading } from "@/payments/Parts";
import { colors, fontSizes, radii } from "@/theme";

/**
 * Change what happens when the term ends: roll everything into a new term, or pay it out to
 * one of the client's approved accounts. Allowed up to the day before maturity. Saving the
 * same choice twice is harmless, so it needs no idempotency key.
 */
export default function MaturityChoiceScreen() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const client = useInvesting();
  const [loaded, retry] = useLoaded(
    useCallback(async (): Promise<{ investment: Investment; accounts: PayoutAccount[] }> => {
      const [investment, accounts] = await Promise.all([client.getInvestment(id), client.listPayoutAccounts()]);
      return { investment, accounts };
    }, [client, id]),
  );
  // Null until the client picks: until then the screen shows what is saved now.
  const [pickedChoice, setChoice] = useState<AtMaturity | null>(null);
  const [pickedDestination, setDestinationId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = loaded.state === "ready" ? loaded.value : null;
  const choice: AtMaturity = pickedChoice ?? ready?.investment.atMaturity ?? "roll_over";
  const saved = ready?.accounts.some((account) => account.id === ready.investment.payoutDestinationId)
    ? ready.investment.payoutDestinationId
    : null;
  const destinationId = pickedDestination ?? saved ?? (ready ? firstChoosable(ready.accounts, true) : null);
  const canSave =
    ready !== null && ready.investment.canChangeMaturity && (choice === "roll_over" || destinationId !== null);

  const save = async () => {
    if (!canSave) return;
    setBusy(true);
    setError(null);
    try {
      await client.chooseAtMaturity({
        investmentId: id,
        atMaturity: choice,
        destinationId: choice === "pay_out" ? destinationId : null,
      });
      router.replace(`/investment/${id}`);
    } catch (caught) {
      setError(describeInvestingError(caught));
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={canSave ? <Button label="Save" onPress={save} loading={busy} testID="maturity-save" /> : null}
    >
      <Stack.Screen options={{ title: "When the term ends" }} />
      <BackButton fallback={`/investment/${id}`} />
      <View style={{ marginTop: 20 }}>
        <Title>When the term ends</Title>
      </View>
      {loaded.state === "loading" ? (
        <Loading label="Loading your investment" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : !loaded.value.investment.canChangeMaturity ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="warning">{describeInvestingError(new InvestingError("cannot_change_maturity"))}</Notice>
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 18 }}>
          <Txt size={fontSizes.md} color={colors.muted}>
            {loaded.value.investment.maturityDate && loaded.value.investment.totalAtMaturity
              ? `${loaded.value.investment.productName} matures on ${formatDay(loaded.value.investment.maturityDate)} with ${formatAmount(loaded.value.investment.totalAtMaturity)} after tax. You can change this until the day before.`
              : `${loaded.value.investment.productName}. You can change this until the day before it matures.`}
          </Txt>
          <View style={{ gap: 8 }} accessibilityRole="radiogroup">
            {(["roll_over", "pay_out"] as const).map((option) => {
              const on = option === choice;
              return (
                <Pressable
                  key={option}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: on }}
                  onPress={() => setChoice(option)}
                  style={[styles.option, on && styles.selected]}
                  testID={`maturity-${option}`}
                >
                  <View style={[styles.radio, on && styles.radioOn]} />
                  <View style={{ flex: 1, gap: 2 }}>
                    <Txt weight="bold">{atMaturityLabel[option]}</Txt>
                    <Txt size={fontSizes.sm} color={colors.muted}>
                      {atMaturityDetail[option]}
                    </Txt>
                  </View>
                </Pressable>
              );
            })}
          </View>
          {choice === "pay_out" ? (
            <View style={{ gap: 8 }}>
              <Eyebrow>Pay it to</Eyebrow>
              {loaded.value.accounts.length === 0 ? (
                <NoAccount />
              ) : (
                <AccountPicker accounts={loaded.value.accounts} chosen={destinationId} onChoose={setDestinationId} allowWaiting />
              )}
            </View>
          ) : null}
          {error ? <Notice tone="danger">{error}</Notice> : null}
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 16,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  selected: { borderColor: colors.black, borderWidth: 2, padding: 15 },
  radio: { width: 20, height: 20, borderRadius: 10, borderWidth: 2, borderColor: colors.borderStrong },
  radioOn: { borderColor: colors.black, borderWidth: 6 },
});
