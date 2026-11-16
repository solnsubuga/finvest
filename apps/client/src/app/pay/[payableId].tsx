import { useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164, nationalDigits, toE164 } from "@/auth/phone";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { PhoneField } from "@/components/Field";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatMoney, networkName, networkProduct } from "@/payments/format";
import { newIdempotencyKey } from "@/payments/idempotency";
import { AmountCard, Loading } from "@/payments/Parts";
import { usePayments } from "@/payments/PaymentsProvider";
import {
  CardOffer,
  describePaymentError,
  openPaymentHref,
  Network,
  Payable,
  PaymentError,
} from "@/payments/types";
import { colors, fontSizes, radii } from "@/theme";

const NETWORKS: Network[] = ["mtn", "airtel"];

type Method = "mobile_money" | "card";

/** A first guess from the number, so most clients do not have to choose. They can change it. */
function likelyNetwork(national: string): Network {
  return /^7[045]/.test(national) ? "airtel" : "mtn";
}

/** A refusal means nothing was saved. Anything else may have reached us before the answer was lost. */
function outcomeUnknown(error: unknown): boolean {
  return !(error instanceof PaymentError) || error.code === "unavailable";
}

/**
 * Pay for your investment. The amount comes from the investment and cannot be
 * changed. The prompt goes to the number on the client's account, or to another
 * number they give: any number may pay (the owner's decision, 2026-11-15), and
 * the payment still counts for this investment.
 */
export default function Pay() {
  const router = useRouter();
  // `method=card` opens with card chosen, as "Try again" after a card that was not paid does.
  const { payableId, method: asked } = useLocalSearchParams<{ payableId: string; method?: string }>();
  const { session } = useAuth();
  const { client } = usePayments();

  const ownNumber = session?.phone ?? "";
  // Another number to pay from, as typed, while "Pay from another number" is open.
  const [otherNumber, setOtherNumber] = useState<string | null>(null);
  const [numberError, setNumberError] = useState<string | null>(null);
  const [payable, setPayable] = useState<Payable | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [network, setNetwork] = useState<Network>(likelyNetwork(nationalDigits(ownNumber)));
  // Set once a try from this visit may have been saved. From then on every try must be the
  // same request under the same key, so the network can no longer be changed.
  const [locked, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key for this visit: a second tap or a retry asks for the money once.
  const [idempotencyKey] = useState(newIdempotencyKey);
  const [cardKey] = useState(newIdempotencyKey);
  const [method, setMethod] = useState<Method>(asked === "card" ? "card" : "mobile_money");
  const [cardOffer, setCardOffer] = useState<CardOffer | null>(null);

  useEffect(() => {
    let live = true;
    client.getPayable(payableId).then(
      (found) => live && setPayable(found),
      (caught) => live && setLoadError(describePaymentError(caught)),
    );
    // Cards are offered only when the server says what they would cost. Without an answer the
    // screen offers mobile money and bank transfer, as before.
    client.getCardOffer?.(payableId).then(
      (offer) => {
        if (!live) return;
        setCardOffer(offer);
        // Card cannot stay chosen when it cannot be used now.
        if (offer.refused !== null) setMethod("mobile_money");
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [client, payableId]);

  const payByCard = async () => {
    if (!payable || !client.payWithCard) return;
    setError(null);
    setBusy(true);
    try {
      const payment = await client.payWithCard({ payableId: payable.id, idempotencyKey: cardKey });
      router.replace(`/pay/card/${payment.id}`);
    } catch (caught) {
      const open = openPaymentHref(caught);
      if (open) {
        router.replace(open);
        return;
      }
      if (caught instanceof PaymentError && caught.code === "card_refused" && client.getCardOffer) {
        // Paused or over a limit since the screen opened: show why, and offer the other ways.
        client.getCardOffer(payable.id).then(setCardOffer, () => undefined);
        setMethod("mobile_money");
      }
      if (outcomeUnknown(caught)) {
        setLocked(true);
      }
      setError(describePaymentError(caught));
      setBusy(false);
    }
  };

  // Bank transfers are not offered for bonds yet: a bond's price holds for a day, not three.
  const bank = payable?.kind !== "bond";
  const what = payable?.kind === "bond" ? "bond" : "investment";
  const cardShown = cardOffer !== null && cardOffer.refused !== "cards_unavailable";
  const cardOpen = cardShown && cardOffer.refused === null;
  const paying = cardOpen && method === "card" ? "card" : "mobile_money";

  const pay = async () => {
    setError(null);
    if (paying === "card") {
      await payByCard();
      return;
    }
    const payer = otherNumber === null ? ownNumber : toE164(otherNumber);
    if (!payer) {
      setNumberError("Enter an MTN or Airtel number, like 772 123 456.");
      return;
    }
    if (!payable) {
      return;
    }
    setNumberError(null);
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
      // Already paying: show that payment instead of a form that cannot pay again.
      const open = openPaymentHref(caught);
      if (open) {
        router.replace(open);
        return;
      }
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
            label={
              paying === "card" && cardOffer
                ? `Pay ${formatMoney(cardOffer.total)} by card`
                : `Pay ${formatMoney(payable.amount)}`
            }
            variant="lime"
            onPress={pay}
            loading={busy}
            testID="pay-submit"
          />
        ) : null
      }
    >
      <Stack.Screen options={{ title: `Pay for your ${what}` }} />
      <BackButton fallback="/invest" />
      <View style={{ marginTop: 20 }}>
        <Title>{`Pay for your ${what}`}</Title>
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
            note={`Your ${what} starts as soon as the payment is confirmed.`}
          />
          {error ? <Notice tone="danger">{error}</Notice> : null}
          {locked ? (
            <Notice tone="warning">
              {`Your first request may already be with ${networkName[network]}, so the network and number are fixed. Pressing Pay again repeats that same request. It does not ask for the money twice.`}
            </Notice>
          ) : null}
          {cardShown && cardOffer.refused ? <CardRefusedNotice offer={cardOffer} bank={bank} what={what} /> : null}
          {cardShown ? (
            <View style={{ gap: 8 }} accessibilityRole="radiogroup">
              <Eyebrow>Pay with</Eyebrow>
              <MethodOption
                label="Mobile money"
                hint="MTN or Airtel, prompted on your phone"
                badge="MTN Airtel"
                selected={paying === "mobile_money"}
                disabled={locked}
                onPress={() => setMethod("mobile_money")}
                testID="method-mobile-money"
              />
              <MethodOption
                label="Card"
                hint={
                  cardOffer.refused === "channel_paused"
                    ? bank
                      ? "Paused for now. Try mobile money or a bank transfer."
                      : "Paused for now. Try mobile money."
                    : cardOffer.refused
                      ? `Up to ${formatMoney(cardOffer.maxPayment)} a payment, charge included`
                      : "Visa or Mastercard, debit or credit"
                }
                badge="VISA MC"
                selected={paying === "card"}
                disabled={locked || cardOffer.refused !== null}
                onPress={() => setMethod("card")}
                testID="method-card"
              />
            </View>
          ) : null}
          {paying === "card" && cardOffer ? (
            <View style={{ gap: 12 }} testID="card-charge">
              <View style={styles.breakdown}>
                <BreakdownRow label={what === "bond" ? "Bond" : "Investment"} value={formatMoney(cardOffer.amount)} />
                <BreakdownRow label="Card charge" value={formatMoney(cardOffer.charge)} />
                <BreakdownRow label="You pay" value={formatMoney(cardOffer.total)} strong />
              </View>
              <Txt size={fontSizes.xs} weight="medium" color={colors.muted}>
                {`${cardOffer.charge.minor > 0 ? "The card charge goes to the card network and Pesapal, not to Finvest. " : ""}Up to ${formatMoney(cardOffer.maxPayment)} a payment, card charge included. If this money is ever refunded, it goes back to the same card.`}
              </Txt>
            </View>
          ) : (
            <>
            <View style={{ gap: 8 }} accessibilityRole="radiogroup">
              <Eyebrow>Pay from</Eyebrow>
              {NETWORKS.map((option) => {
                const selected = option === network;
                return (
                  <Pressable
                    key={option}
                    accessibilityRole="radio"
                    aria-checked={selected}
                    aria-disabled={locked}
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
            {otherNumber === null ? (
              <>
                <View style={styles.number} accessible testID="pay-number">
                  <Txt size={fontSizes.sm} color={colors.muted}>
                    Your number
                  </Txt>
                  <Txt size={fontSizes.lg} weight="bold">
                    {formatE164(ownNumber)}
                  </Txt>
                </View>
                <Txt size={fontSizes.xs} weight="medium" color={colors.muted}>
                  {`${networkName[network]} sends a prompt to the number on your Finvest account. You approve it with your mobile money PIN.`}
                </Txt>
              </>
            ) : locked ? (
              // The first request may already be with the network: the number is fixed with it.
              <View style={styles.number} accessible testID="pay-number">
                <Txt size={fontSizes.sm} color={colors.muted}>
                  Number to pay from
                </Txt>
                <Txt size={fontSizes.lg} weight="bold">
                  {formatE164(toE164(otherNumber) ?? otherNumber)}
                </Txt>
              </View>
            ) : (
              <PhoneField
                label="Number to pay from"
                value={otherNumber}
                onChangeText={(text) => {
                  setOtherNumber(text);
                  setNumberError(null);
                  // Guess the network again from the typed number. The client can still change it.
                  if (nationalDigits(text).length >= 2) setNetwork(likelyNetwork(nationalDigits(text)));
                }}
                hint={`${networkName[network]} sends the prompt to this number, and whoever has the phone approves it with their mobile money PIN. The payment counts for your ${what}.`}
                error={numberError}
                testID="pay-other-number"
              />
            )}
            {locked ? null : (
              <Pressable
                accessibilityRole="button"
                onPress={() => {
                  if (otherNumber !== null) setNetwork(likelyNetwork(nationalDigits(ownNumber)));
                  setOtherNumber(otherNumber === null ? "" : null);
                  setNumberError(null);
                }}
                testID="pay-number-toggle"
                hitSlop={8}
              >
                <Txt size={fontSizes.sm} weight="bold" color={colors.ink} style={styles.toggle}>
                  {otherNumber === null ? "Pay from another number" : "Pay from my own number"}
                </Txt>
              </Pressable>
            )}
            </>
          )}
          {locked || !bank ? null : (
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

/** Why cards cannot take this payment, and the other ways it can be paid. Bonds have no bank transfer yet. */
function CardRefusedNotice({ offer, bank, what }: { offer: CardOffer; bank: boolean; what: "bond" | "investment" }) {
  if (offer.refused === "channel_paused") {
    return (
      <Notice tone="warning" title="Card payments are paused">
        {bank
          ? "Card payments are not working right now. Mobile money and bank transfer work as usual."
          : "Card payments are not working right now. Mobile money works as usual."}
      </Notice>
    );
  }
  if (offer.refused === "above_card_payment_limit") {
    return (
      <Notice tone="warning" title={`Cards take up to ${formatMoney(offer.maxPayment)} a payment`}>
        {bank
          ? "With the card charge, this payment is above that. Pay it by mobile money or bank transfer, or invest a smaller amount by card."
          : `With the card charge, this payment is above that. Pay it by mobile money, or buy a smaller ${what} by card.`}
      </Notice>
    );
  }
  return (
    <Notice tone="warning" title="You have paid as much by card as 30 days allow">
      {bank ? "Pay this one by mobile money or bank transfer." : "Pay this one by mobile money."}
    </Notice>
  );
}

function MethodOption({
  label,
  hint,
  badge,
  selected,
  disabled,
  onPress,
  testID,
}: {
  label: string;
  hint: string;
  badge: string;
  selected: boolean;
  disabled: boolean;
  onPress: () => void;
  testID: string;
}) {
  return (
    <Pressable
      accessibilityRole="radio"
      aria-checked={selected}
      aria-disabled={disabled}
      disabled={disabled}
      accessibilityLabel={`${label}. ${hint}`}
      onPress={onPress}
      testID={testID}
      style={({ pressed }) => [
        styles.option,
        selected && styles.optionSelected,
        disabled && !selected && styles.optionDisabled,
        pressed && { opacity: 0.8 },
      ]}
    >
      <View style={styles.badgeWide}>
        <Txt size={fontSizes.xs} weight="extrabold" style={{ textAlign: "center" }}>
          {badge}
        </Txt>
      </View>
      <View style={{ flex: 1, paddingVertical: 10 }}>
        <Txt weight="bold" color={disabled && !selected ? colors.muted : colors.ink}>
          {label}
        </Txt>
        <Txt size={fontSizes.xs} color={colors.muted}>
          {hint}
        </Txt>
      </View>
      {disabled && !selected ? null : (
        <View style={[styles.radio, selected && styles.radioSelected]}>
          {selected ? <Icon name="check" size={14} color={colors.ink} strokeWidth={3} /> : null}
        </View>
      )}
    </Pressable>
  );
}

function BreakdownRow({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <View style={styles.breakdownRow} accessible accessibilityLabel={`${label}: ${value}`}>
      <Txt size={fontSizes.sm} weight={strong ? "bold" : "regular"} color={strong ? colors.ink : colors.muted}>
        {label}
      </Txt>
      <Txt size={fontSizes.sm} weight={strong ? "extrabold" : "semibold"}>
        {value}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  optionDisabled: { backgroundColor: colors.surfaceSunken, borderColor: colors.surfaceSunken },
  badgeWide: {
    width: 64,
    minHeight: 36,
    borderRadius: radii.sm,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 4,
  },
  breakdown: {
    borderRadius: radii.lg,
    backgroundColor: colors.surfaceSunken,
    paddingHorizontal: 14,
  },
  breakdownRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    minHeight: 46,
  },
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
  toggle: { textDecorationLine: "underline" },
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
