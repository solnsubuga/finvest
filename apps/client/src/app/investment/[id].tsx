import { useCallback } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { atMaturityLabel, formatAmount, formatDay, statusLabel } from "@/investing/format";
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
  if (investment.maturityDate) {
    rows.push({ label: investment.status === "matured" ? "Matured" : "Matures", value: formatDay(investment.maturityDate) });
  }
  if (investment.status !== "awaiting_payment") {
    rows.push({ label: "Interest so far, after tax", value: formatAmount(investment.interestSoFar) });
  }
  if (investment.interestAtMaturity && investment.totalAtMaturity && investment.status !== "matured") {
    rows.push({ label: "Interest at maturity, after tax", value: formatAmount(investment.interestAtMaturity) });
    rows.push({ label: "You get back", value: formatAmount(investment.totalAtMaturity) });
  }
  if (investment.atMaturity) {
    rows.push({ label: "When the term ends", value: atMaturityLabel[investment.atMaturity] });
  }

  return (
    <View style={{ marginTop: 20, gap: 16 }}>
      <View style={{ gap: 6 }}>
        <Eyebrow>{statusLabel[investment.status]}</Eyebrow>
        <Title>{investment.productName}</Title>
      </View>
      <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 }}>
        <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
          {investment.status === "awaiting_payment" ? "To pay" : "Worth today"}
        </Txt>
        <Txt size={36} weight="extrabold" lineHeight={42} color={colors.white} style={{ letterSpacing: -0.8 }}>
          {formatAmount(investment.status === "awaiting_payment" ? investment.amount : investment.value)}
        </Txt>
        <Txt size={fontSizes.sm} weight="semibold" color={colors.lime}>
          {investment.status === "awaiting_payment"
            ? `Pay by ${formatDateTime(investment.payBy)} to keep this rate`
            : investment.interestToday.minor > 0
              ? `+${formatAmount(investment.interestToday)} today, ${investment.daysEarned} days earned`
              : `${investment.daysEarned} days earned`}
        </Txt>
      </View>
      <DetailList rows={rows} />
      {investment.waitingForDestination ? (
        <Notice tone="warning">Your money is ready. Choose where to be paid and we will send it.</Notice>
      ) : null}
      {investment.status === "active" && investment.atMaturity ? (
        <Txt size={fontSizes.xs} color={colors.muted}>
          {investment.canChangeMaturity
            ? "You can change what happens at the end until the day before it matures."
            : "It matures soon, so what happens at the end is fixed."}
        </Txt>
      ) : null}
      <Txt size={fontSizes.xs} color={colors.muted}>
        Interest is added every night after tax. Your rate is fixed for the whole term.
      </Txt>
    </View>
  );
}
