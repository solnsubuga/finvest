import { useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { BackButton } from "@/components/BackButton";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { formatDateTime, formatMoney } from "@/payments/format";
import { openCardPage } from "@/payments/openCardPage";
import { Detail, DetailList, Loading, ShareNote } from "@/payments/Parts";
import { asText, shareText, ShareOutcome } from "@/payments/share";
import { CardPayment } from "@/payments/types";
import { useCardPayment } from "@/payments/useDeposit";
import { colors, fontSizes, radii } from "@/theme";

/** "Visa card ending 0010", or just "Card" when Pesapal did not say. */
export function cardWords(payment: CardPayment): string {
  const brand = payment.card?.brand;
  const lastFour = payment.card?.lastFour;
  if (brand && lastFour) return `${brand} card ending ${lastFour}`;
  if (lastFour) return `Card ending ${lastFour}`;
  return brand ? `${brand} card` : "Card";
}

/**
 * A card payment from start to end, at one address: opening Pesapal's page, waiting while the
 * client pays there, confirming, and then paid or not paid. A client who closes the app and
 * comes back sees the truth, whatever it is now.
 */
export default function CardPaymentScreen() {
  const router = useRouter();
  const { paymentId, returned } = useLocalSearchParams<{ paymentId: string; returned?: string }>();
  const { payment, missing, stale } = useCardPayment(paymentId);
  // Set once the client has been sent to the page, or is back from it, so the screen says it is
  // confirming rather than offering the page again.
  const [opened, setOpened] = useState(returned === "1");
  const home = () => router.replace("/");

  if (missing) {
    return (
      <Screen signedIn footer={<Button label="Close" onPress={home} />}>
        <Stack.Screen options={{ title: "Card payment" }} />
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">We could not find this payment.</Notice>
        </View>
      </Screen>
    );
  }
  if (!payment) {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: "Card payment" }} />
        {stale ? <Notice tone="neutral">We could not reach Finvest. Trying again.</Notice> : <Loading label="Loading your payment" />}
      </Screen>
    );
  }
  if (payment.status === "confirmed") return <Paid payment={payment} onDone={home} />;
  if (payment.status === "failed") {
    return (
      <NotPaid
        payment={payment}
        onRetry={() => router.replace(`/pay/${payment.payable.id}?method=card`)}
        onAnotherWay={() => router.replace(`/pay/${payment.payable.id}`)}
        onClose={home}
      />
    );
  }
  if (!opened && payment.status !== "checking") {
    return (
      <Finish
        payment={payment}
        onOpen={() => {
          if (payment.pageUrl) {
            setOpened(true);
            openCardPage(payment.pageUrl);
          }
        }}
        onAnotherWay={() => router.replace(`/pay/${payment.payable.id}`)}
      />
    );
  }
  return (
    <Confirming
      payment={payment}
      stale={stale}
      onOpenAgain={payment.pageUrl ? () => openCardPage(payment.pageUrl as string) : null}
      onHome={home}
    />
  );
}

function Finish({
  payment,
  onOpen,
  onAnotherWay,
}: {
  payment: CardPayment;
  onOpen: () => void;
  onAnotherWay: () => void;
}) {
  const total = formatMoney(payment.total);
  const ready = payment.pageUrl !== null;
  const steps = [
    `Pesapal's payment page opens. Check that it says Finvest and ${total}.`,
    "Type your card details there. Finvest never sees them.",
    "Your bank may ask you to confirm, with a code by SMS or in its app.",
    "You come back here by yourself. Your investment starts once Pesapal confirms the payment.",
  ];
  return (
    <Screen
      signedIn
      footer={
        <>
          <Button
            label={ready ? "Open the secure page" : "Opening the secure page"}
            onPress={onOpen}
            loading={!ready}
            testID="card-open-page"
          />
          <Button label="Pay another way" variant="secondary" onPress={onAnotherWay} testID="card-another-way" />
        </>
      }
    >
      <Stack.Screen options={{ title: "Finish on Pesapal's secure page" }} />
      <BackButton fallback="/" />
      <View style={{ marginTop: 20, gap: 10 }} accessibilityLiveRegion="polite">
        <Title>{"Finish on Pesapal's secure page"}</Title>
        <Txt color={colors.muted}>{`Card payment, ${total} for your ${payment.payable.title}.`}</Txt>
      </View>
      <View style={[styles.steps, { marginTop: 20 }]}>
        {steps.map((step, index) => (
          <View key={step} style={[styles.stepRow, index > 0 && styles.divider]}>
            <View style={styles.stepNumber}>
              <Txt size={fontSizes.sm} weight="bold">
                {String(index + 1)}
              </Txt>
            </View>
            <Txt style={{ flex: 1 }}>{step}</Txt>
          </View>
        ))}
      </View>
      <View style={styles.lockRow}>
        <Icon name="lock" size={18} color={colors.muted} />
        <Txt size={fontSizes.xs} color={colors.muted} style={{ flex: 1 }}>
          Pesapal is licensed by the Bank of Uganda. Your card details stay with Pesapal and your bank.
        </Txt>
      </View>
    </Screen>
  );
}

function Confirming({
  payment,
  stale,
  onOpenAgain,
  onHome,
}: {
  payment: CardPayment;
  stale: boolean;
  onOpenAgain: (() => void) | null;
  onHome: () => void;
}) {
  const checking = payment.status === "checking";
  return (
    <Screen
      signedIn
      footer={
        <>
          {onOpenAgain && !checking ? (
            <>
              <Txt size={fontSizes.xs} color={colors.muted} style={{ textAlign: "center" }}>
                Closed the card page before you finished?
              </Txt>
              <Button label="Open the card page again" variant="secondary" onPress={onOpenAgain} testID="card-open-again" />
            </>
          ) : null}
          <Button label="Back to home" onPress={onHome} testID="card-home" />
        </>
      }
    >
      <Stack.Screen options={{ title: "Confirming your card payment" }} />
      <View style={styles.top} accessibilityLiveRegion="polite">
        <View style={styles.spinner}>
          <ActivityIndicator size="large" color={colors.success} />
        </View>
        <Txt size={fontSizes.sm} weight="bold" color={colors.success}>
          {checking ? "Checking with Pesapal" : "Waiting for Pesapal"}
        </Txt>
        <Txt accessibilityRole="header" size={34} weight="extrabold" lineHeight={40} style={{ letterSpacing: -0.8 }}>
          {formatMoney(payment.total)}
        </Txt>
        <Txt color={colors.muted} style={{ textAlign: "center" }}>
          {checking
            ? "Pesapal has not given us a clear answer yet, so we are asking them. Do not pay again. If your card was charged, your investment starts as soon as Pesapal confirms it."
            : "Confirming your card payment. This usually takes less than a minute. You can leave this screen. We will tell you when it is done."}
        </Txt>
      </View>
      <View style={{ marginTop: 24, gap: 12 }}>
        {stale ? <Notice tone="neutral">We could not refresh this just now. Trying again.</Notice> : null}
        <DetailList
          rows={[
            { label: "Paying with", value: "Card" },
            { label: "For", value: payment.payable.title },
            { label: "Started", value: formatDateTime(payment.requestedAt) },
          ]}
        />
      </View>
    </Screen>
  );
}

function Paid({ payment, onDone }: { payment: CardPayment; onDone: () => void }) {
  const [shared, setShared] = useState<ShareOutcome | null>(null);
  const rows: Detail[] = [{ label: "Paid by", value: cardWords(payment) }];
  if (payment.charge.minor > 0) {
    rows.push({ label: "Card charge", value: formatMoney(payment.charge) });
  }
  rows.push({ label: "Total taken from your card", value: formatMoney(payment.total) });
  if (payment.confirmedAt) rows.push({ label: "Confirmed", value: formatDateTime(payment.confirmedAt) });
  rows.push({ label: "Finvest reference", value: payment.reference });
  const share = async () =>
    setShared(
      await shareText(
        "Finvest receipt",
        asText(`Finvest receipt: ${formatMoney(payment.amount)} paid into ${payment.payable.title}`, rows),
      ),
    );
  return (
    <Screen
      signedIn
      footer={
        <>
          <Button label="Share receipt" variant="secondary" onPress={share} testID="card-share" />
          <Button label="Done" onPress={onDone} testID="card-done" />
        </>
      }
    >
      <Stack.Screen options={{ title: "Receipt" }} />
      <View style={styles.top}>
        <View style={[styles.badge, { backgroundColor: colors.lime }]}>
          <Icon name="check" size={30} color={colors.black} strokeWidth={2.6} />
        </View>
        <Txt size={fontSizes.sm} weight="bold" color={colors.success}>
          Completed
        </Txt>
        <Txt accessibilityRole="header" size={34} weight="extrabold" lineHeight={40} style={{ letterSpacing: -0.8 }}>
          {formatMoney(payment.amount)}
        </Txt>
        <Txt color={colors.muted} style={{ textAlign: "center" }}>
          {`Paid into ${payment.payable.title}. Your investment has started.`}
        </Txt>
      </View>
      <View style={{ marginTop: 24 }}>
        <DetailList rows={rows} />
      </View>
      <ShareNote outcome={shared} />
    </Screen>
  );
}

type NotPaidCopy = { title: string; sentence: string };

const OTHER: NotPaidCopy = {
  title: "Your card payment did not go through",
  sentence: "No money was taken. You can try again or pay by mobile money.",
};

const NOT_PAID: Record<string, NotPaidCopy> = {
  declined: {
    title: "Your card was declined",
    sentence: "No money was taken. Your bank did not approve the payment. You can try another card or pay by mobile money.",
  },
  insufficient_funds: {
    title: "Not enough money on the card",
    sentence: "No money was taken. You can try another card or pay by mobile money.",
  },
  approval_timed_out: {
    title: "The card page was not finished",
    sentence: "No money was taken. The page was open for 30 minutes without a payment, so it was closed. You can start again.",
  },
  other: OTHER,
};

function NotPaid({
  payment,
  onRetry,
  onAnotherWay,
  onClose,
}: {
  payment: CardPayment;
  onRetry: () => void;
  onAnotherWay: () => void;
  onClose: () => void;
}) {
  const copy = NOT_PAID[payment.failure ?? "other"] ?? OTHER;
  return (
    <Screen
      signedIn
      footer={
        <>
          <Button label="Try again" onPress={onRetry} testID="card-retry" />
          <Button label="Pay another way" variant="secondary" onPress={onAnotherWay} testID="card-pay-another-way" />
          <TextLink label="Close" onPress={onClose} />
        </>
      }
    >
      <Stack.Screen options={{ title: "Not paid" }} />
      <View style={styles.top} accessibilityLiveRegion="polite">
        <View style={[styles.badge, { backgroundColor: colors.dangerSurface }]}>
          <Icon name="close" size={30} color={colors.danger} strokeWidth={2.6} />
        </View>
        <Txt size={fontSizes.sm} weight="bold" color={colors.danger}>
          Not paid
        </Txt>
        <Title>{copy.title}</Title>
        <Txt color={colors.muted} style={{ textAlign: "center" }}>
          {copy.sentence}
        </Txt>
      </View>
      <View style={{ marginTop: 24 }}>
        <DetailList
          rows={[
            { label: "Amount", value: formatMoney(payment.total) },
            { label: "When", value: formatDateTime(payment.requestedAt) },
            { label: "Finvest reference", value: payment.reference },
          ]}
        />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { marginTop: 32, alignItems: "center", gap: 6 },
  badge: {
    width: 64,
    height: 64,
    borderRadius: 32,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 6,
  },
  spinner: { width: 64, height: 64, alignItems: "center", justifyContent: "center", marginBottom: 6 },
  steps: {
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    paddingHorizontal: 16,
  },
  stepRow: { flexDirection: "row", gap: 14, paddingVertical: 14, alignItems: "flex-start" },
  divider: { borderTopWidth: 1, borderTopColor: colors.border },
  stepNumber: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
  lockRow: { flexDirection: "row", gap: 10, alignItems: "flex-start", marginTop: 16 },
});
