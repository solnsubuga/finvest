import { useCallback } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
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
        ) : investment?.canWithdraw ? (
          <Button
            label="Withdraw early"
            variant="secondary"
            onPress={() => router.push(`/investment/withdraw/${investment.id}`)}
            testID="withdraw-early"
          />
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
        <Body investment={loaded.value} />
      )}
    </Screen>
  );
}

function Body({ investment }: { investment: Investment }) {
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
    rows.push({ label: "You get back", value: formatAmount(investment.totalAtMaturity) });
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
      {investment.withdrawalRequestedAt ? (
        <Notice tone="neutral">
          {`You asked to withdraw this early, so interest stopped then. ${formatAmount(investment.value)} goes to your account, and we tell you when it is sent.`}
        </Notice>
      ) : null}
      {investment.waitingForDestination ? (
        <Notice tone="warning">Your money is ready. Choose where to be paid and we will send it.</Notice>
      ) : null}
      {investment.status === "active" && investment.atMaturity && !investment.withdrawalRequestedAt ? (
        <Txt size={fontSizes.xs} color={colors.muted}>
          {investment.canChangeMaturity
            ? "You can change what happens at the end until the day before it matures."
            : "It matures soon, so what happens at the end is fixed."}
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
