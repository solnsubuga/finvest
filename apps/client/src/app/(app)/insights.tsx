import { useCallback, useEffect, useRef, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { kampalaToday, formatLongDay } from "@/autoinvest/schedule";
import { AppPage } from "@/components/AppPage";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { LinkCard } from "@/components/LinkCard";
import { Eyebrow, Txt } from "@/components/Txt";
import { barUnit, daysUntil, earnedBars, monthLong } from "@/insights/chart";
import { useInsights } from "@/insights/InsightsProvider";
import { Currency, Insights as InsightsData, Upcoming, UpcomingMaturity, upcomingKey } from "@/insights/types";
import { upcomingHref, upcomingWords } from "@/insights/upcoming";
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
  const [currency, setCurrency] = useState<Currency>("UGX");
  // Shillings until the investments say otherwise. If they cannot be read, the switch
  // stays hidden and shillings still show: nothing is lost by waiting.
  const [held, setHeld] = useState<Currency[]>(["UGX"]);

  useEffect(() => {
    let live = true;
    client.heldCurrencies().then(
      (found) => live && setHeld(found),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [client]);

  // Only the latest request may change the screen, so a late answer for the currency the
  // client has just switched away from is dropped.
  const latest = useRef(0);
  const fetch = useCallback(() => {
    latest.current += 1;
    const request = latest.current;
    return client.getInsights(MONTHS_SHOWN, currency).then(
      (insights) => request === latest.current && setState({ status: "ready", insights }),
      () => request === latest.current && setState({ status: "error" }),
    );
  }, [client, currency]);

  useEffect(() => {
    void fetch();
  }, [fetch]);

  const choose = (next: Currency) => {
    if (next === currency) return;
    setState({ status: "loading" });
    setCurrency(next);
  };

  const load = () => {
    setState({ status: "loading" });
    void fetch();
  };

  const action =
    held.length > 1 ? <CurrencySwitch held={held} chosen={currency} onChoose={choose} /> : undefined;

  if (state.status === "loading") {
    return (
      <AppPage title="Insights" action={action}>
        <Loading label="Loading your insights" />
      </AppPage>
    );
  }
  if (state.status === "error") {
    return (
      <AppPage title="Insights" action={action}>
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

  // The switch shows dollars only to a client who holds a dollar investment, so an empty
  // dollar view means it has not started earning yet. It is not a first investment to make.
  if (empty && insights.currency === "USD") {
    return (
      <AppPage title="Insights" subtitle="What your money earns, month by month." action={action}>
        <Card>
          <View style={{ gap: 8 }}>
            <Txt size={fontSizes.lg} weight="extrabold">
              No dollar earnings yet
            </Txt>
            <Txt size={fontSizes.sm} color={colors.muted}>
              A dollar investment starts earning the day after its payment is confirmed. From then on
              you will see here what it earns each month, in dollars and after tax.
            </Txt>
          </View>
        </Card>
      </AppPage>
    );
  }

  if (empty) {
    return (
      <AppPage title="Insights" subtitle="What your money earns, month by month." action={action}>
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
  const next = insights.upcoming.find(
    (item): item is UpcomingMaturity => item.kind === "maturity",
  );
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
          {`${barUnit[insights.currency]}, after tax. ${monthLong(current.month)} is part way through.`}
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
          : next.atMaturity === "pay_interest"
            ? `${next.productName} pays its interest to your account on ${formatLongDay(next.on)}, and what you put in starts another term.`
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
            <UpcomingRow
              key={upcomingKey(item)}
              item={item}
              first={index === 0}
              onPress={() => router.push(upcomingHref(item))}
            />
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
    <AppPage
      title="Insights"
      subtitle="What your money earns, month by month. All after tax."
      action={action}
    >
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

const CURRENCY_NAMES: Record<Currency, string> = { UGX: "Shillings", USD: "Dollars" };

/**
 * Shillings or dollars. Each shows its own figures: the two are never added together,
 * because no rate between them is the client's to rely on.
 */
function CurrencySwitch({
  held,
  chosen,
  onChoose,
}: {
  held: Currency[];
  chosen: Currency;
  onChoose: (currency: Currency) => void;
}) {
  return (
    <View style={styles.switch} accessibilityRole="radiogroup" accessibilityLabel="Currency">
      {held.map((currency) => {
        const on = currency === chosen;
        return (
          <Pressable
            key={currency}
            accessibilityRole="radio"
            aria-checked={on}
            accessibilityLabel={CURRENCY_NAMES[currency]}
            onPress={() => onChoose(currency)}
            style={[styles.switchOption, on && styles.switchOn]}
            testID={`insights-currency-${currency}`}
          >
            <Txt size={13} weight="bold" color={on ? colors.white : colors.ink}>
              {currency}
            </Txt>
          </Pressable>
        );
      })}
    </View>
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

function UpcomingRow({
  item,
  first,
  onPress,
}: {
  item: Upcoming;
  first: boolean;
  onPress: () => void;
}) {
  const day = item.on.slice(8, 10).replace(/^0/, "");
  const month = formatLongDay(item.on).split(" ")[1];
  const words = upcomingWords(item);
  const amount = formatMoney(item.amount);
  return (
    <Pressable
      style={[styles.row, !first && styles.divider]}
      accessibilityRole="link"
      accessibilityLabel={`${formatLongDay(item.on)}. ${words.title}. ${words.detail}. ${amount}`}
      onPress={onPress}
      testID={`insights-upcoming-${upcomingKey(item)}`}
    >
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
          {words.title}
        </Txt>
        <Txt size={12} color={colors.muted}>
          {words.detail}
        </Txt>
      </View>
      <Txt size={14} weight="bold">
        {amount}
      </Txt>
    </Pressable>
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
  switch: {
    flexDirection: "row",
    alignSelf: "flex-start",
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: 22,
    padding: 3,
    gap: 2,
  },
  switchOption: { minHeight: 38, minWidth: 52, paddingHorizontal: 12, borderRadius: 19, justifyContent: "center", alignItems: "center" },
  switchOn: { backgroundColor: colors.black },
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
