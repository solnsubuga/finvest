import { useCallback, useEffect, useState } from "react";
import { StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { kampalaToday, formatLongDay } from "@/autoinvest/schedule";
import { AppPage } from "@/components/AppPage";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { LinkCard } from "@/components/LinkCard";
import { Eyebrow, Txt } from "@/components/Txt";
import { daysUntil, earnedBars, monthLong } from "@/insights/chart";
import { useInsights } from "@/insights/InsightsProvider";
import { Insights as InsightsData, Upcoming } from "@/insights/types";
import { formatMoney } from "@/payments/format";
import { Loading } from "@/payments/Parts";
import { colors, fontSizes, radii } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

const MONTHS_SHOWN = 6;
const BAR_HEIGHT = 110;

type State =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; insights: InsightsData };

/**
 * Insights: interest earned month by month, what was invested and paid out,
 * and what comes next. Every figure is after tax and comes from the ledger.
 */
export default function Insights() {
  const client = useInsights();
  const router = useRouter();
  const wide = useIsWide();
  const [state, setState] = useState<State>({ status: "loading" });

  const fetch = useCallback(
    () =>
      client.getInsights(MONTHS_SHOWN).then(
        (insights) => setState({ status: "ready", insights }),
        () => setState({ status: "error" }),
      ),
    [client],
  );

  useEffect(() => {
    void fetch();
  }, [fetch]);

  const load = () => {
    setState({ status: "loading" });
    void fetch();
  };

  if (state.status === "loading") {
    return (
      <AppPage title="Insights">
        <Loading label="Loading your insights" />
      </AppPage>
    );
  }
  if (state.status === "error") {
    return (
      <AppPage title="Insights">
        <Notice tone="danger">
          We could not load your insights. Check your connection and try again.
        </Notice>
        <Button label="Try again" variant="secondary" onPress={load} testID="insights-retry" />
      </AppPage>
    );
  }

  const { insights } = state;
  const empty =
    insights.investedNow.minor === 0 &&
    insights.months.every((month) => month.earned.minor === 0 && month.invested.minor === 0);

  if (empty) {
    return (
      <AppPage title="Insights" subtitle="What your money earns, month by month.">
        <Card>
          <View style={{ gap: 8 }}>
            <Txt size={fontSizes.lg} weight="extrabold">
              Nothing earned yet
            </Txt>
            <Txt size={fontSizes.sm} color={colors.muted}>
              Your first investment starts earning the day after its payment is confirmed. From then
              on you will see here what it earns each month, after tax.
            </Txt>
          </View>
        </Card>
        <Button label="Make your first investment" onPress={() => router.navigate("/invest")} />
        <LinkCard
          icon="repeat"
          title="Invest automatically"
          description="The same amount every week or month, from your mobile money"
          onPress={() => router.push("/autoinvest")}
        />
      </AppPage>
    );
  }

  const current = insights.months[insights.months.length - 1];
  if (!current) {
    return null;
  }
  const next = insights.upcoming[0];
  const today = kampalaToday();

  const chart = (
    <Card>
      <View style={{ gap: 12 }} testID="insights-chart">
        <View>
          <Txt size={13} color={colors.muted}>
            {`Interest earned in ${monthLong(current.month)} so far`}
          </Txt>
          <Txt size={30} weight="extrabold" lineHeight={38} style={{ letterSpacing: -0.6 }}>
            {formatMoney(current.earned)}
          </Txt>
        </View>
        <View
          style={styles.bars}
          accessibilityRole="image"
          accessibilityLabel={barsLabel(insights)}
        >
          {earnedBars(insights.months, BAR_HEIGHT).map((bar) => (
            <View key={bar.month} style={styles.barColumn}>
              <Txt size={11} weight="bold">
                {bar.value}
              </Txt>
              <View
                style={[
                  styles.bar,
                  {
                    height: bar.height,
                    backgroundColor: bar.current ? colors.lime : colors.black,
                  },
                ]}
              />
              <Txt size={12} color={colors.muted}>
                {bar.label}
              </Txt>
            </View>
          ))}
        </View>
        <Txt size={12} color={colors.muted}>
          {`Thousands of shillings, after tax. ${monthLong(current.month)} is part way through.`}
        </Txt>
      </View>
    </Card>
  );

  const totals = (
    <View style={styles.totals}>
      <Stat label="Invested now" value={formatMoney(insights.investedNow)} />
      <Stat label="Earned so far" value={formatMoney(insights.earnedSoFar)} />
    </View>
  );

  const thisMonth = (
    <View style={{ gap: 8 }}>
      <Eyebrow>{`${monthLong(current.month)} so far`}</Eyebrow>
      <Card padded={false}>
        <Row label="You invested" value={formatMoney(current.invested)} first />
        <Row label="You earned" value={formatMoney(current.earned)} />
        <Row label="Paid to you" value={formatMoney(current.paidOut)} />
      </Card>
    </View>
  );

  const callout = next ? (
    <View style={styles.callout} testID="insights-next">
      <Txt size={15} weight="extrabold" color={colors.limeText}>
        {`Your next investment matures in ${daysUntil(next.on, today)} days`}
      </Txt>
      <Txt size={13} color={colors.limeText}>
        {next.atMaturity === "pay_out"
          ? `${next.productName} pays ${formatMoney(next.amount)} to your account on ${formatLongDay(next.on)}. You can still choose to roll it over before then.`
          : `${next.productName} rolls over on ${formatLongDay(next.on)} with ${formatMoney(next.amount)}, so it keeps earning. You can still choose to be paid out before then.`}
      </Txt>
    </View>
  ) : null;

  const comingUp =
    insights.upcoming.length > 0 ? (
      <View style={{ gap: 8 }}>
        <Eyebrow>Coming up</Eyebrow>
        <Card padded={false}>
          {insights.upcoming.map((item, index) => (
            <UpcomingRow key={item.investmentId} item={item} first={index === 0} />
          ))}
        </Card>
      </View>
    ) : null;

  const autoinvest = (
    <LinkCard
      icon="repeat"
      title="Invest automatically"
      description="The same amount every week or month, from your mobile money"
      onPress={() => router.push("/autoinvest")}
      testID="insights-autoinvest"
    />
  );

  return (
    <AppPage title="Insights" subtitle="What your money earns, month by month. All after tax.">
      {wide ? (
        <View style={styles.columns}>
          <View style={styles.column}>
            {chart}
            {totals}
            {thisMonth}
          </View>
          <View style={styles.column}>
            {callout}
            {comingUp}
            {autoinvest}
          </View>
        </View>
      ) : (
        <>
          {chart}
          {callout}
          {totals}
          {thisMonth}
          {comingUp}
          {autoinvest}
        </>
      )}
    </AppPage>
  );
}

function barsLabel(insights: InsightsData): string {
  return insights.months
    .map((month) => `${monthLong(month.month)}: ${formatMoney(month.earned)}`)
    .join(", ");
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat} accessible accessibilityLabel={`${label}: ${value}`}>
      <Txt size={13} color={colors.muted}>
        {label}
      </Txt>
      <Txt size={17} weight="extrabold">
        {value}
      </Txt>
    </View>
  );
}

function Row({ label, value, first }: { label: string; value: string; first?: boolean }) {
  return (
    <View
      style={[styles.row, !first && styles.divider]}
      accessible
      accessibilityLabel={`${label}: ${value}`}
    >
      <Txt size={fontSizes.sm} color={colors.muted}>
        {label}
      </Txt>
      <Txt size={fontSizes.md} weight="bold">
        {value}
      </Txt>
    </View>
  );
}

function UpcomingRow({ item, first }: { item: Upcoming; first: boolean }) {
  const day = item.on.slice(8, 10).replace(/^0/, "");
  const month = formatLongDay(item.on).split(" ")[1];
  return (
    <View style={[styles.row, !first && styles.divider]} accessible>
      <View style={styles.date}>
        <Txt size={18} weight="extrabold" lineHeight={22}>
          {day}
        </Txt>
        <Txt size={11} color={colors.muted}>
          {month}
        </Txt>
      </View>
      <View style={{ flex: 1 }}>
        <Txt size={14} weight="bold">
          {`${item.productName} matures`}
        </Txt>
        <Txt size={12} color={colors.muted}>
          {item.atMaturity === "pay_out"
            ? "Pays out to your account"
            : "Rolls over and keeps earning"}
        </Txt>
      </View>
      <Txt size={14} weight="bold">
        {formatMoney(item.amount)}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  bars: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 10,
    height: BAR_HEIGHT + 40,
  },
  barColumn: {
    flex: 1,
    alignItems: "center",
    justifyContent: "flex-end",
    gap: 4,
  },
  bar: {
    alignSelf: "stretch",
    borderTopLeftRadius: 8,
    borderTopRightRadius: 8,
    borderBottomLeftRadius: 3,
    borderBottomRightRadius: 3,
  },
  totals: { flexDirection: "row", gap: 12 },
  stat: {
    flex: 1,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.xl,
    padding: 16,
    gap: 2,
  },
  callout: {
    backgroundColor: colors.limeSurface,
    borderRadius: 22,
    padding: 18,
    gap: 6,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    paddingVertical: 13,
    paddingHorizontal: 16,
  },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  date: { width: 44, alignItems: "center" },
  columns: { flexDirection: "row", gap: 20, alignItems: "flex-start" },
  column: { flex: 1, gap: 16 },
});
