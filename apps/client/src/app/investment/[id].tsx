import { useCallback } from "react";
import { View } from "react-native";
import { Href, Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { DocumentLink, useDocumentFor } from "@/documents/DocumentLink";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { atMaturityLabel, formatAmount, formatDay, investmentLabel } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { Investment } from "@/investing/types";
import { useLoaded } from "@/investing/useLoaded";
import { formatDateTime } from "@/payments/format";
import { Detail, DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes } from "@/theme";

/** One investment: what it is worth, what it has earned after tax, and what happens at the end. */
export default function InvestmentDetail() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const client = useInvesting();
  const [loaded, retry] = useLoaded(useCallback(() => client.getInvestment(id), [client, id]));

  const investment = loaded.state === "ready" ? loaded.value : null;
  return (
    <Screen
      signedIn
      footer={
        investment?.status === "awaiting_payment" ? (
          <Button
            label={`Pay ${formatAmount(investment.amount)}`}
            onPress={() => router.push(`/pay/${investment.id}`)}
            testID="pay-now"
          />
        ) : investment?.waitingForDestination ? (
          <Button
            label="Choose where to be paid"
            onPress={() => router.push(`/investment/pay-out/${investment.id}`)}
            testID="choose-pay-out"
          />
        ) : investment?.canSend || investment?.canWithdraw ? (
          <View style={{ gap: 10 }}>
            {investment.canSend ? (
              <Button
                label="Send to someone"
                onPress={() => router.push(`/investment/send/${investment.id}`)}
                testID="send-investment"
              />
            ) : null}
            {investment.canWithdraw ? (
              <Button
                label="Withdraw early"
                variant="secondary"
                onPress={() => router.push(`/investment/withdraw/${investment.id}`)}
                testID="withdraw-early"
              />
            ) : null}
          </View>
        ) : null
      }
    >
      <Stack.Screen options={{ title: investment?.productName ?? "Investment" }} />
      <BackButton fallback="/portfolio" />
      {loaded.state === "loading" ? (
        <Loading label="Loading your investment" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : (
        <Body investment={loaded.value} onOpen={(href) => router.push(href)} />
      )}
    </Screen>
  );
}

function Body({ investment, onOpen }: { investment: Investment; onOpen: (href: Href) => void }) {
  // The note that confirms it started. A rollover and an investment not yet started have none.
  const note = useDocumentFor("investment_note", investment.id);
  const rows: Detail[] = [
    { label: "You invested", value: formatAmount(investment.amount) },
    { label: "Rate after tax", value: `${investment.ratePercent}% a year` },
  ];
  if (investment.startDate) {
    rows.push({ label: "Started", value: formatDay(investment.startDate) });
  }
  // Once an early withdrawal is asked for, the term no longer runs to maturity, so nothing
  // about maturity is shown.
  const toMaturity = !investment.withdrawalRequestedAt;
  if (investment.maturityDate && toMaturity) {
    rows.push({ label: investment.status === "matured" ? "Matured" : "Matures", value: formatDay(investment.maturityDate) });
  }
  if (investment.status !== "awaiting_payment") {
    rows.push({ label: "Interest so far, after tax", value: formatAmount(investment.interestSoFar) });
  }
  if (investment.interestAtMaturity && investment.totalAtMaturity && investment.status !== "matured" && toMaturity) {
    rows.push({ label: "Interest at maturity, after tax", value: formatAmount(investment.interestAtMaturity) });
    if (investment.atMaturity === "pay_interest") {
      // The interest is paid out and what was put in starts another term.
      rows.push({ label: "Paid to you", value: formatAmount(investment.interestAtMaturity) });
      rows.push({ label: "Stays invested", value: formatAmount(investment.amount) });
    } else {
      rows.push({ label: "You get back", value: formatAmount(investment.totalAtMaturity) });
    }
  }
  if (investment.atMaturity && toMaturity) {
    rows.push({ label: "When the term ends", value: atMaturityLabel[investment.atMaturity] });
  }

  return (
    <View style={{ marginTop: 20, gap: 16 }}>
      <View style={{ gap: 6 }}>
        <Eyebrow>{investmentLabel(investment)}</Eyebrow>
        <Title>{investment.productName}</Title>
      </View>
      <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 }}>
        <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
          {investment.status === "awaiting_payment"
            ? "To pay"
            : investment.withdrawalRequestedAt
              ? "You get"
              : "Worth today"}
        </Txt>
        <Txt size={36} weight="extrabold" lineHeight={42} color={colors.white} style={{ letterSpacing: -0.8 }}>
          {formatAmount(investment.status === "awaiting_payment" ? investment.amount : investment.value)}
        </Txt>
        <Txt size={fontSizes.sm} weight="semibold" color={colors.lime}>
          {investment.status === "awaiting_payment"
            ? `Pay by ${formatDateTime(investment.payBy)} to keep this rate`
            : investment.withdrawalRequestedAt
              ? `Asked for on ${formatDateTime(investment.withdrawalRequestedAt)}, after ${investment.daysEarned} days`
              : investment.interestToday.minor > 0
              ? `+${formatAmount(investment.interestToday)} today, ${investment.daysEarned} days earned`
              : `${investment.daysEarned} days earned`}
        </Txt>
      </View>
      <DetailList rows={rows} />
      {note ? <DocumentLink document={note} title="Transaction note" testID="investment-note" /> : null}
      {investment.status === "transferred" ? (
        <Notice tone="neutral">
          You sent this to another Finvest client. What you kept, if any, is in a new investment on the same terms.
        </Notice>
      ) : null}
      {investment.withdrawalRequestedAt ? (
        <Notice tone="neutral">
          {`You asked to withdraw this early, so interest stopped then. ${formatAmount(investment.value)} goes to your account, and we tell you when it is sent.`}
        </Notice>
      ) : null}
      {investment.waitingForDestination ? (
        <Notice tone="warning">
          {investment.rolledIntoId && investment.waiting
            ? `Your interest of ${formatAmount(investment.waiting)} is ready, and what you put in started another term. Choose where to be paid and we will send it.`
            : "Your money is ready. Choose where to be paid and we will send it."}
        </Notice>
      ) : null}
      {investment.payoutId ? (
        <TextLink
          label="Follow the payout"
          onPress={() => onOpen(`/payouts/payout/${investment.payoutId}`)}
          testID="open-investment-payout"
        />
      ) : null}
      {investment.atMaturity && investment.canChangeMaturity && !investment.withdrawalRequestedAt ? (
        <View style={{ gap: 6, alignItems: "flex-start" }}>
          <TextLink
            label="Change what happens at the end"
            onPress={() => onOpen(`/investment/maturity/${investment.id}`)}
            testID="change-maturity"
          />
          <Txt size={fontSizes.xs} color={colors.muted}>
            You can change it until the day before it matures.
          </Txt>
        </View>
      ) : investment.status === "active" && investment.atMaturity && !investment.withdrawalRequestedAt ? (
        <Txt size={fontSizes.xs} color={colors.muted}>
          It matures soon, so what happens at the end is fixed.
        </Txt>
      ) : null}
      {investment.withdrawalRequestedAt ? null : (
        <Txt size={fontSizes.xs} color={colors.muted}>
          Interest is added every night after tax. Your rate is fixed for the whole term.
        </Txt>
      )}
    </View>
  );
}
