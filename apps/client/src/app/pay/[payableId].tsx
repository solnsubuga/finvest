import { useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164, nationalDigits } from "@/auth/phone";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatMoney, networkName, networkProduct } from "@/payments/format";
import { newIdempotencyKey } from "@/payments/idempotency";
import { AmountCard, Loading } from "@/payments/Parts";
import { usePayments } from "@/payments/PaymentsProvider";
import { describePaymentError, Network, Payable, PaymentError } from "@/payments/types";
import { colors, fontSizes, radii } from "@/theme";

const NETWORKS: Network[] = ["mtn", "airtel"];

/** A first guess from the number, so most clients do not have to choose. They can change it. */
function likelyNetwork(national: string): Network {
  return /^7[045]/.test(national) ? "airtel" : "mtn";
}

/** A refusal means nothing was saved. Anything else may have reached us before the answer was lost. */
function outcomeUnknown(error: unknown): boolean {
  return !(error instanceof PaymentError) || error.code === "unavailable";
}

/**
 * Pay for your investment. The amount comes from the investment and the
 * number is the one on the client's own account, so neither can be changed
 * here. A payment from a number registered to someone else is refused. The
 * client only confirms the network.
 */
export default function Pay() {
  const router = useRouter();
  const { payableId } = useLocalSearchParams<{ payableId: string }>();
  const { session } = useAuth();
  const { client } = usePayments();

  const payer = session?.phone ?? "";
  const [payable, setPayable] = useState<Payable | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [network, setNetwork] = useState<Network>(likelyNetwork(nationalDigits(payer)));
  // Set once a try from this visit may have been saved. From then on every try must be the
  // same request under the same key, so the network can no longer be changed.
  const [locked, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key for this visit: a second tap or a retry asks for the money once.
  const [idempotencyKey] = useState(newIdempotencyKey);

  useEffect(() => {
    let live = true;
    client.getPayable(payableId).then(
      (found) => live && setPayable(found),
      (caught) => live && setLoadError(describePaymentError(caught)),
    );
    return () => {
      live = false;
    };
  }, [client, payableId]);

  const pay = async () => {
    setError(null);
    if (!payable || !payer) {
      return;
    }
    setBusy(true);
    try {
      const deposit = await client.payWithMobileMoney({
        payableId: payable.id,
        network,
        payer,
        idempotencyKey,
      });
      // Replace, so Back from the status screen cannot land on a form that pays again.
      router.replace(`/pay/deposit/${deposit.id}`);
    } catch (caught) {
      if (outcomeUnknown(caught)) {
        setLocked(true);
      }
      setError(describePaymentError(caught));
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        payable ? (
          <Button
            label={`Pay ${formatMoney(payable.amount)}`}
            onPress={pay}
            loading={busy}
            testID="pay-submit"
          />
        ) : null
      }
    >
      <Stack.Screen options={{ title: "Pay for your investment" }} />
      <BackButton fallback="/invest" />
      <View style={{ marginTop: 20 }}>
        <Title>Pay for your investment</Title>
      </View>

      {loadError ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">{loadError}</Notice>
        </View>
      ) : !payable ? (
        <Loading label="Loading what you are paying for" />
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <AmountCard
            title={payable.title}
            amount={payable.amount}
            note="Your investment starts as soon as the payment is confirmed."
          />
          {error ? <Notice tone="danger">{error}</Notice> : null}
          {locked ? (
            <Notice tone="warning">
              {`Your first request may already be with ${networkName[network]}, so the network is fixed. Pressing Pay again repeats that same request. It does not ask for the money twice.`}
            </Notice>
          ) : null}
          <View style={{ gap: 8 }} accessibilityRole="radiogroup">
            <Eyebrow>Pay from</Eyebrow>
            {NETWORKS.map((option) => {
              const selected = option === network;
              return (
                <Pressable
                  key={option}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: selected, disabled: locked }}
                  disabled={locked}
                  accessibilityLabel={networkProduct[option]}
                  onPress={() => setNetwork(option)}
                  testID={`network-${option}`}
                  style={({ pressed }) => [
                    styles.option,
                    selected && styles.optionSelected,
                    locked && !selected && { opacity: 0.4 },
                    pressed && { opacity: 0.8 },
                  ]}
                >
                  <View style={styles.badge}>
                    <Txt size={fontSizes.xs} weight="extrabold">
                      {networkName[option]}
                    </Txt>
                  </View>
                  <Txt weight="bold" style={{ flex: 1 }}>
                    {networkProduct[option]}
                  </Txt>
                  <View style={[styles.radio, selected && styles.radioSelected]}>
                    {selected ? <Icon name="check" size={14} color={colors.ink} strokeWidth={3} /> : null}
                  </View>
                </Pressable>
              );
            })}
          </View>
          <View style={styles.number} accessible testID="pay-number">
            <Txt size={fontSizes.sm} color={colors.muted}>
              Your number
            </Txt>
            <Txt size={fontSizes.lg} weight="bold">
              {formatE164(payer)}
            </Txt>
          </View>
          <Txt size={fontSizes.xs} weight="medium" color={colors.muted}>
            {`${networkName[network]} sends a prompt to the number on your Finvest account. You approve it with your mobile money PIN.`}
          </Txt>
          {locked ? null : (
            <Pressable
              accessibilityRole="link"
              onPress={() => router.push(`/pay/bank/${payable.id}`)}
              testID="pay-by-bank"
              style={({ pressed }) => [styles.option, pressed && { opacity: 0.8 }]}
            >
              <View style={styles.badge}>
                <Txt size={fontSizes.xs} weight="extrabold">
                  Bank
                </Txt>
              </View>
              <View style={{ flex: 1 }}>
                <Txt weight="bold">Pay by bank transfer instead</Txt>
                <Txt size={fontSizes.xs} color={colors.muted}>
                  Send from your bank, then upload the receipt
                </Txt>
              </View>
              <Icon name="chevron" color={colors.muted} />
            </Pressable>
          )}
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
    minHeight: 64,
    paddingHorizontal: 14,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
  },
  number: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: 54,
    paddingHorizontal: 14,
    borderRadius: radii.lg,
    backgroundColor: colors.surfaceSunken,
  },
  optionSelected: { borderWidth: 2, borderColor: colors.black, paddingHorizontal: 13 },
  badge: {
    width: 52,
    height: 36,
    borderRadius: radii.sm,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
  radio: {
    width: 24,
    height: 24,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: colors.borderStrong,
    alignItems: "center",
    justifyContent: "center",
  },
  radioSelected: { backgroundColor: colors.lime, borderColor: colors.lime },
});
