import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { AppPage } from "@/components/AppPage";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Txt } from "@/components/Txt";
import { plural, termProgress } from "@/home/format";
import { InvestmentsTable, Panel, PanelLine, SplitBar, Tile } from "@/home/Parts";
import { atMaturityLabel, formatAmount, formatDay, investmentLabel } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed, InvestmentCard } from "@/investing/Parts";
import { Investment, Portfolio as Totals } from "@/investing/types";
import { useFocusLoaded } from "@/investing/useLoaded";
import { Loading } from "@/payments/Parts";
import { colors } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

/** Home's Withdraw and Send open Portfolio to choose which investment. */
const PICK: Record<string, string> = {
  withdraw: "Choose the investment you want to withdraw from.",
  send: "Choose the investment you want to send to someone.",
};

/** The client's investments, newest first, with their totals and how they are split. After tax. */
export default function Portfolio() {
  const router = useRouter();
  const wide = useIsWide();
  const params = useLocalSearchParams<{ for?: string }>();
  const pick = params.for ? PICK[params.for] : undefined;
  const client = useInvesting();
  const [totals, retryTotals] = useFocusLoaded(useCallback(() => client.getPortfolio("UGX"), [client]));
  const [investments, retry] = useFocusLoaded(useCallback(() => client.listInvestments(), [client]));
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const open = (investment: Investment) => router.push(`/investment/${investment.id}`);
  const loading = totals.state === "loading" || investments.state === "loading";
  const failed = totals.state === "failed" ? totals : investments.state === "failed" ? investments : null;
  const retryAll = () => {
    retryTotals();
    retry();
  };

  const content = loading ? (
    <Loading label="Loading your investments" />
  ) : failed ? (
    <Failed message={failed.message} onRetry={retryAll} />
  ) : investments.state === "ready" && investments.value.length === 0 ? (
    <View style={{ gap: 12 }}>
      <Notice tone="neutral">You have no investments yet. Pick a product to make your first one.</Notice>
      <Button label="See what you can invest in" onPress={() => router.navigate("/invest")} testID="go-invest" />
    </View>
  ) : null;

  if (wide) {
    const list = investments.state === "ready" ? investments.value : [];
    const selected = list.find((item) => item.id === selectedId) ?? list.find((item) => item.status === "active") ?? list[0] ?? null;
    return (
      <AppPage
        title="Portfolio"
        subtitle="Choose an investment to see its detail beside the list. Every figure is after tax."
        action={
          <View style={styles.actions}>
            <PillButton label="Invest more" primary onPress={() => router.navigate("/invest")} testID="portfolio-invest" />
            {selected?.canWithdraw ? (
              <PillButton label="Withdraw" onPress={() => router.push(`/investment/withdraw/${selected.id}`)} testID="portfolio-withdraw" />
            ) : null}
          </View>
        }
        roomy
      >
        {pick ? <Notice tone="neutral">{pick}</Notice> : null}
        {content}
        {!content && totals.state === "ready" ? (
          <>
            <WideTiles totals={totals.value} />
            {totals.value.products.length ? (
              <View style={styles.splitCard}>
                <Txt size={13} color={colors.muted}>
                  Where it is invested
                </Txt>
                <SplitBar portfolio={totals.value} onDark={false} />
              </View>
            ) : null}
            <View style={styles.wideRow}>
              <View style={{ flex: 3, minWidth: 480 }}>
                <InvestmentsTable investments={list} selectedId={selected?.id ?? null} onOpen={(item) => setSelectedId(item.id)} />
              </View>
              <View style={{ flex: 2, minWidth: 300 }}>{selected ? <Detail investment={selected} onOpen={open} /> : null}</View>
            </View>
          </>
        ) : null}
      </AppPage>
    );
  }

  return (
    <AppPage title="Portfolio" subtitle="What your investments are worth today, after tax.">
      {pick ? <Notice tone="neutral">{pick}</Notice> : null}
      {content}
      {!content && totals.state === "ready" && investments.state === "ready" ? (
        <View style={{ gap: 12 }}>
          <TotalsCard totals={totals.value} />
          <Txt size={12} weight="bold" color={colors.muted} style={styles.eyebrow} accessibilityRole="header">
            Your investments
          </Txt>
          {investments.value.map((investment) => (
            <InvestmentCard key={investment.id} investment={investment} onPress={() => open(investment)} />
          ))}
          <Pressable
            accessibilityRole="link"
            onPress={() => router.navigate("/invest")}
            style={({ pressed }) => [styles.add, pressed && { opacity: 0.7 }]}
            testID="portfolio-add"
          >
            <Txt weight="bold">Add an investment</Txt>
          </Pressable>
        </View>
      ) : null}
    </AppPage>
  );
}

/** The black card on a phone: worth today, interest so far, and the split by product. */
function TotalsCard({ totals }: { totals: Totals }) {
  return (
    <View style={styles.black} testID="portfolio-totals">
      <Txt size={13} color={colors.onBlackMuted}>
        Invested in shillings
      </Txt>
      <Txt size={34} weight="extrabold" lineHeight={42} color={colors.white} style={{ letterSpacing: -0.8 }}>
        {formatAmount(totals.worth)}
      </Txt>
      <Txt size={13} weight="bold" color={colors.lime}>
        {totals.interestSoFar.minor > 0
          ? `${formatAmount(totals.interestSoFar)} interest earned so far`
          : totals.activeCount
            ? "Interest starts the day after your payment"
            : "Nothing running yet"}
      </Txt>
      {totals.products.length ? (
        <View style={{ marginTop: 10 }}>
          <SplitBar portfolio={totals} />
        </View>
      ) : null}
      {totals.awaitingPaymentCount ? (
        <View style={styles.blackFoot}>
          <Txt size={13} color={colors.onBlackMuted}>
            Waiting for payment
          </Txt>
          <Txt size={13} weight="bold" color={colors.white}>
            {plural(totals.awaitingPaymentCount, "investment")}
          </Txt>
        </View>
      ) : null}
    </View>
  );
}

function WideTiles({ totals }: { totals: Totals }) {
  const bills = totals.products.reduce((count, share) => count + share.count, 0);
  return (
    <View style={styles.tiles}>
      <Tile label="You put in" value={formatAmount(totals.invested)} detail={`Across ${plural(bills, "investment")}`} testID="tile-put-in" />
      <Tile label="Worth today" value={formatAmount(totals.worth)} detail="After tax" testID="tile-worth" />
      <Tile
        label="Interest so far"
        value={formatAmount(totals.interestSoFar)}
        detail={totals.earningPerDay.minor ? `About ${formatAmount(totals.earningPerDay)} a day` : "Added every night"}
        testID="tile-interest"
      />
      <Tile
        label="Waiting for payment"
        value={String(totals.awaitingPaymentCount)}
        detail={totals.awaitingPaymentCount ? "Pay to start earning" : "Nothing waiting"}
        testID="tile-waiting"
      />
    </View>
  );
}

/** The chosen investment beside the table on a wide screen. */
function Detail({ investment, onOpen }: { investment: Investment; onOpen: (investment: Investment) => void }) {
  const router = useRouter();
  const running = investment.status === "active" && investment.termDays !== null;
  return (
    <Panel title={investment.productName} testID="portfolio-detail">
      <Txt size={30} weight="extrabold" lineHeight={38} style={{ letterSpacing: -0.8 }}>
        {formatAmount(investment.value)}
      </Txt>
      {running && investment.termDays ? (
        <>
          <View style={styles.track}>
            <View style={[styles.bar, { width: termProgress(investment.daysEarned, investment.termDays) }]} />
          </View>
          <Txt size={13} color={colors.muted}>
            {`Day ${investment.daysEarned} of ${investment.termDays}.`}
          </Txt>
        </>
      ) : (
        <Txt size={13} color={colors.muted}>
          {investmentLabel(investment)}
        </Txt>
      )}
      <PanelLine
        label="You put in"
        value={investment.startDate ? `${formatAmount(investment.amount)} on ${formatDay(investment.startDate)}` : formatAmount(investment.amount)}
      />
      <PanelLine label="Interest so far" value={formatAmount(investment.interestSoFar)} />
      <PanelLine label="Rate" value={`${investment.ratePercent}% a year, after tax`} />
      {investment.maturityDate ? (
        <PanelLine
          label="Matures"
          value={investment.totalAtMaturity ? `${formatDay(investment.maturityDate)}, ${formatAmount(investment.totalAtMaturity)}` : formatDay(investment.maturityDate)}
        />
      ) : null}
      {investment.atMaturity ? <PanelLine label="At maturity" value={atMaturityLabel[investment.atMaturity]} /> : null}
      <View style={{ gap: 8, marginTop: 6 }}>
        {investment.status === "awaiting_payment" ? (
          <Button label="Pay to start" height={48} onPress={() => router.push(`/pay/${investment.id}`)} testID="detail-pay" />
        ) : null}
        {investment.canChangeMaturity ? (
          <Button label="Change what happens at maturity" height={48} onPress={() => router.push(`/investment/maturity/${investment.id}`)} testID="detail-maturity" />
        ) : null}
        {investment.canWithdraw ? (
          <Button label="Withdraw early" variant="secondary" height={48} onPress={() => router.push(`/investment/withdraw/${investment.id}`)} testID="detail-withdraw" />
        ) : null}
        <Button label="Open full detail" variant="secondary" height={48} onPress={() => onOpen(investment)} testID="detail-open" />
      </View>
    </Panel>
  );
}

function PillButton({ label, onPress, primary = false, testID }: { label: string; onPress: () => void; primary?: boolean; testID: string }) {
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [styles.pill, primary ? { backgroundColor: colors.lime } : styles.pillWhite, pressed && { opacity: 0.8 }]}
      testID={testID}
    >
      <Txt size={14} weight="bold">
        {label}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  pill: { height: 44, paddingHorizontal: 18, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  pillWhite: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.borderStrong },
  black: { backgroundColor: colors.black, borderRadius: 24, padding: 20, gap: 6 },
  blackFoot: {
    marginTop: 6,
    borderTopWidth: 1,
    borderTopColor: colors.onBlackDivider,
    paddingTop: 10,
    flexDirection: "row",
    justifyContent: "space-between",
  },
  eyebrow: { textTransform: "uppercase", letterSpacing: 0.6 },
  add: {
    height: 52,
    borderRadius: 26,
    borderWidth: 1,
    borderStyle: "dashed",
    borderColor: colors.borderStrong,
    alignItems: "center",
    justifyContent: "center",
  },
  tiles: { flexDirection: "row", flexWrap: "wrap", gap: 16 },
  splitCard: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: 18, padding: 16, gap: 8 },
  wideRow: { flexDirection: "row", flexWrap: "wrap", gap: 20, alignItems: "flex-start" },
  track: { height: 8, borderRadius: 4, backgroundColor: colors.border, flexDirection: "row" },
  bar: { borderRadius: 4, backgroundColor: colors.black },
});
