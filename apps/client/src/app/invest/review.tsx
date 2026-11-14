import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { atMaturityDetail, atMaturityLabel, formatAmount, formatDay } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { AtMaturity, describeInvestingError, InvestingError, Product, Quote } from "@/investing/types";
import { useLoaded } from "@/investing/useLoaded";
import { newIdempotencyKey } from "@/payments/idempotency";
import { DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes, radii } from "@/theme";
import { IdCheckPrompt } from "@/identity/Parts";

const CHOICES: AtMaturity[] = ["roll_over", "pay_out"];

/**
 * Review and confirm. Confirming saves the investment at today's rate, then
 * the client pays for it. Nothing is paid on this screen.
 */
export default function Review() {
  const router = useRouter();
  const params = useLocalSearchParams<{ code: string; amount: string }>();
  const client = useInvesting();
  const [atMaturity, setAtMaturity] = useState<AtMaturity>("roll_over");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Refused because the ID check is not approved yet: the error then opens it.
  const [needsId, setNeedsId] = useState(false);
  // One key per request: a double tap or a retry after a lost answer saves one investment.
  // After a try whose outcome is unknown the choice is locked, so trying again sends the
  // same request. After a definite refusal nothing was saved, so the next try gets a new key.
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [locked, setLocked] = useState(false);

  const minor = Number(params.amount);
  const [loaded, retry] = useLoaded(
    useCallback(async (): Promise<{ product: Product; quote: Quote }> => {
      const product = (await client.listProducts()).find((p) => p.code === params.code);
      if (!product || !Number.isSafeInteger(minor) || minor <= 0) {
        throw new InvestingError("not_found");
      }
      return { product, quote: await client.quote(product.code, { currency: product.currency, minor }) };
    }, [client, params.code, minor]),
  );

  const confirm = async () => {
    if (loaded.state !== "ready") {
      return;
    }
    const { product, quote } = loaded.value;
    setBusy(true);
    setError(null);
    try {
      const investment = await client.requestInvestment({
        productCode: product.code,
        amount: quote.amount,
        atMaturity: product.termDays === null ? null : atMaturity,
        idempotencyKey,
      });
      // Replace, so Back from paying does not land on a confirm button that saves another one.
      router.replace(`/pay/${investment.id}`);
    } catch (caught) {
      setNeedsId(caught instanceof InvestingError && caught.code === "not_verified");
      const unknown = !(caught instanceof InvestingError) || caught.code === "unavailable";
      if (unknown) {
        setLocked(true);
        setError(`${describeInvestingError(caught)} Trying again sends the same request, so you will not invest twice.`);
      } else {
        setIdempotencyKey(newIdempotencyKey());
        setError(describeInvestingError(caught));
      }
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        loaded.state === "ready" ? (
          <Button label="Confirm and pay" onPress={confirm} loading={busy} testID="confirm" />
        ) : null
      }
    >
      <Stack.Screen options={{ title: "Review your investment" }} />
      <BackButton fallback={`/invest/${params.code}`} />
      <View style={{ marginTop: 20 }}>
        <Title>Review your investment</Title>
      </View>
      {loaded.state === "loading" ? (
        <Loading label="Loading your investment" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <DetailList
            rows={[
              { label: "Product", value: loaded.value.product.name },
              { label: "You invest", value: formatAmount(loaded.value.quote.amount) },
              { label: "Rate after tax", value: `${loaded.value.product.ratePercent}% a year` },
              ...(loaded.value.quote.interest && loaded.value.quote.total && loaded.value.quote.maturesOn
                ? [
                    { label: "Interest after tax", value: formatAmount(loaded.value.quote.interest) },
                    { label: "You get back", value: formatAmount(loaded.value.quote.total) },
                    { label: "Matures on", value: formatDay(loaded.value.quote.maturesOn) },
                  ]
                : []),
            ]}
          />
          {loaded.value.product.termDays !== null ? (
            <View style={{ gap: 8 }} accessibilityRole="radiogroup">
              <Eyebrow>When the term ends</Eyebrow>
              {CHOICES.map((choice) => {
                const selected = choice === atMaturity;
                return (
                  <Pressable
                    key={choice}
                    accessibilityRole="radio"
                    accessibilityState={{ checked: selected, disabled: locked }}
                    disabled={locked}
                    accessibilityLabel={atMaturityLabel[choice]}
                    onPress={() => setAtMaturity(choice)}
                    testID={`at-maturity-${choice}`}
                    style={({ pressed }) => [styles.option, selected && styles.selected, pressed && { opacity: 0.85 }]}
                  >
                    <View style={[styles.radio, selected && styles.radioOn]} />
                    <View style={{ flex: 1, gap: 2 }}>
                      <Txt size={fontSizes.md} weight="bold">
                        {atMaturityLabel[choice]}
                      </Txt>
                      <Txt size={fontSizes.sm} color={colors.muted}>
                        {atMaturityDetail[choice]}
                      </Txt>
                    </View>
                  </Pressable>
                );
              })}
              <Txt size={fontSizes.xs} color={colors.muted}>
                {locked
                  ? "Your last try may have gone through, so this choice stays as it was. You can change it later from the investment."
                  : "You can change this until the day before it matures."}
              </Txt>
            </View>
          ) : null}
          {error ? (needsId ? <IdCheckPrompt message={error} /> : <Notice tone="danger">{error}</Notice>) : null}
          <Txt size={fontSizes.xs} color={colors.muted}>
            This rate is held for 72 hours while you pay. Your term starts on the day your payment is confirmed.
          </Txt>
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
