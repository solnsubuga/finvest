import { ReactNode } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { Icon, IconName } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { atMaturityLabel, formatAmount, formatDay, investmentLabel } from "@/investing/format";
import { Investment, NextMaturity, Portfolio } from "@/investing/types";
import { colors } from "@/theme";

import { figure, plural, shortDay, termProgress } from "./format";

/** The black card at the top of Home: what is invested and what it earns a day. */
export function TotalCard({ portfolio, wide = false }: { portfolio: Portfolio; wide?: boolean }) {
  const worth = portfolio.worth;
  return (
    <View style={[styles.black, wide && { flex: 2, minWidth: 320 }]} testID="home-total">
      <Txt size={13} color={colors.onBlackMuted}>
        Total invested
      </Txt>
      {wide ? (
        <Txt size={40} weight="extrabold" lineHeight={48} color={colors.white} style={styles.tight} accessibilityLabel={`Total invested, ${formatAmount(worth)}`}>
          {formatAmount(worth)}
        </Txt>
      ) : (
        <View style={styles.figureRow} accessible accessibilityLabel={`Total invested, ${formatAmount(worth)}`}>
          <Txt size={15} weight="semibold" color={colors.onBlackMuted}>
            {worth.currency}
          </Txt>
          <Txt size={38} weight="extrabold" lineHeight={46} color={colors.white} style={styles.tight}>
            {figure(worth)}
          </Txt>
        </View>
      )}
      <Txt size={13} color={colors.onBlackMuted}>
        {portfolio.interestSoFar.minor > 0
          ? `${formatAmount(portfolio.interestSoFar)} interest earned so far, after tax`
          : "Interest is added every night, after tax"}
      </Txt>
      {portfolio.earningPerDay.minor > 0 ? (
        <View style={styles.earning} testID="home-earning">
          <Icon name="arrowUp" size={14} strokeWidth={2.5} color={colors.ink} />
          <Txt size={13} weight="bold">
            {`Earning ${formatAmount(portfolio.earningPerDay)} a day`}
          </Txt>
        </View>
      ) : null}
    </View>
  );
}

/** A new client's top card: nothing invested yet, what a first investment could earn, and the way in. */
export function FirstTimeCard({ example, onInvest }: { example: string | null; onInvest: () => void }) {
  return (
    <View style={[styles.black, { gap: 8, paddingVertical: 22 }]} testID="home-first-time">
      <Txt size={13} color={colors.onBlackMuted}>
        Total invested
      </Txt>
      <Txt size={38} weight="extrabold" lineHeight={46} color={colors.white} style={styles.tight}>
        UGX 0
      </Txt>
      {example ? (
        <Txt size={14} lineHeight={20} color={colors.onBlackMuted} testID="home-example">
          {example}
        </Txt>
      ) : null}
      <Pressable
        accessibilityRole="button"
        onPress={onInvest}
        style={({ pressed }) => [styles.limeButton, pressed && { opacity: 0.85 }]}
        testID="home-first-invest"
      >
        <Txt size={15} weight="bold">
          Make your first investment
        </Txt>
      </Pressable>
    </View>
  );
}

export type QuickAction = { label: string; icon: IconName; onPress: () => void; testID: string };

/** Invest, Statements, Withdraw, Send: four round buttons under the total. */
export function QuickActions({ actions }: { actions: QuickAction[] }) {
  return (
    <View style={styles.actions}>
      {actions.map((action, index) => (
        <Pressable
          key={action.label}
          accessibilityRole="button"
          accessibilityLabel={action.label}
          onPress={action.onPress}
          style={({ pressed }) => [styles.action, pressed && { opacity: 0.7 }]}
          testID={action.testID}
        >
          <View style={[styles.actionCircle, index === 0 ? styles.actionLime : styles.actionWhite]}>
            <Icon name={action.icon} size={22} strokeWidth={index === 0 ? 2.2 : 2} />
          </View>
          <Txt size={12} weight="semibold">
            {action.label}
          </Txt>
        </Pressable>
      ))}
    </View>
  );
}

/** Investments and Goals, each with what it is worth: the two add up to the total. */
export function HoldingRows({
  portfolio,
  nextMaturesOn,
  onInvestments,
  onGoals,
}: {
  portfolio: Portfolio;
  nextMaturesOn: string | null;
  onInvestments: () => void;
  onGoals: () => void;
}) {
  const investments = portfolio.outsideGoals;
  const goalsDetail =
    portfolio.openGoals === 0
      ? "Save towards something"
      : portfolio.inGoals.count === 0
        ? `${plural(portfolio.openGoals, "goal")}, nothing saved yet`
        : `${plural(portfolio.openGoals, "goal")}, ${plural(portfolio.inGoals.count, "saving")} running`;
  return (
    <View style={styles.listCard}>
      <HoldingRow
        dot={<View style={[styles.dot, { backgroundColor: colors.black }]} />}
        title="Investments"
        detail={`${investments.count} active${nextMaturesOn ? `, next matures ${shortDay(nextMaturesOn)}` : ""}`}
        value={figure(investments.worth)}
        onPress={onInvestments}
        testID="home-investments"
        divider
      />
      <HoldingRow
        dot={<View style={[styles.dot, { backgroundColor: colors.lime, borderWidth: 1, borderColor: colors.limeBorder }]} />}
        title="Goals"
        detail={goalsDetail}
        value={figure(portfolio.inGoals.worth)}
        onPress={onGoals}
        testID="home-goals"
      />
    </View>
  );
}

function HoldingRow({
  dot,
  title,
  detail,
  value,
  onPress,
  testID,
  divider = false,
}: {
  dot: ReactNode;
  title: string;
  detail: string;
  value: string;
  onPress: () => void;
  testID: string;
  divider?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={`${title}, ${detail}, ${value}`}
      onPress={onPress}
      style={({ pressed }) => [styles.holding, divider && styles.divider, pressed && { opacity: 0.7 }]}
      testID={testID}
    >
      {dot}
      <View style={{ flex: 1 }}>
        <Txt weight="bold">{title}</Txt>
        <Txt size={12} color={colors.muted}>
          {detail}
        </Txt>
      </View>
      <Txt weight="bold" style={styles.tabular}>
        {value}
      </Txt>
    </Pressable>
  );
}

/** The investment that matures soonest, how far through its term it is, and what happens then. */
export function MaturingNext({ next, onPress }: { next: NextMaturity; onPress: () => void }) {
  const rollsOver = next.atMaturity !== "pay_out";
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={`Maturing next, ${next.productName}, ${plural(next.daysLeft, "day")} left`}
      onPress={onPress}
      style={({ pressed }) => [styles.card, { gap: 10 }, pressed && { opacity: 0.8 }]}
      testID="home-maturing-next"
    >
      <View style={styles.between}>
        <Txt size={12} weight="bold" color={colors.muted} style={styles.eyebrow}>
          Maturing next
        </Txt>
        <View style={[styles.pill, rollsOver ? styles.pillGrey : styles.pillLime]}>
          <Txt size={12} weight="bold" color={rollsOver ? colors.black : colors.limeText}>
            {rollsOver ? "Reinvest is on" : "Pays out"}
          </Txt>
        </View>
      </View>
      <View style={[styles.between, { alignItems: "baseline" }]}>
        <Txt weight="bold" style={{ flexShrink: 1 }}>
          {next.productName}
        </Txt>
        <Txt size={13} color={colors.muted}>
          {next.daysLeft === 0 ? "Matures today" : `${plural(next.daysLeft, "day")} left`}
        </Txt>
      </View>
      <View style={styles.track}>
        <View style={[styles.bar, { width: termProgress(next.daysEarned, next.termDays) }]} />
      </View>
    </Pressable>
  );
}

export type SetUpStep = { key: string; title: string; detail: string; done: boolean; onPress: () => void };

/** The first-time checklist: ID, investing questions, next of kin, a goal. */
export function SetUpList({ steps }: { steps: SetUpStep[] }) {
  return (
    <View style={{ gap: 10 }} testID="home-set-up">
      <Txt size={12} weight="bold" color={colors.muted} style={styles.eyebrow} accessibilityRole="header">
        Get set up
      </Txt>
      <View style={[styles.listCard, { paddingVertical: 2 }]}>
        {steps.map((step, index) => (
          <Pressable
            key={step.key}
            accessibilityRole="link"
            accessibilityLabel={`${step.title}, ${step.done ? "done" : step.detail}`}
            onPress={step.onPress}
            style={({ pressed }) => [styles.step, index < steps.length - 1 && styles.softDivider, pressed && { opacity: 0.7 }]}
            testID={`set-up-${step.key}`}
          >
            <View style={{ flex: 1 }}>
              <Txt weight="bold">{step.title}</Txt>
              <Txt size={13} lineHeight={18} color={colors.muted}>
                {step.done ? "Done" : step.detail}
              </Txt>
            </View>
            {step.done ? (
              <View style={[styles.pill, styles.pillLime]}>
                <Txt size={12} weight="bold" color={colors.limeText}>
                  Done
                </Txt>
              </View>
            ) : null}
            <Icon name="chevron" size={16} strokeWidth={2.2} />
          </Pressable>
        ))}
      </View>
    </View>
  );
}

/** A small white tile on the wide Home and Portfolio. */
export function Tile({ label, value, detail, testID }: { label: string; value: string; detail: string; testID?: string }) {
  return (
    <View style={styles.tile} testID={testID}>
      <Txt size={13} color={colors.muted}>
        {label}
      </Txt>
      <Txt size={22} weight="extrabold" lineHeight={28} style={{ letterSpacing: -0.4 }}>
        {value}
      </Txt>
      <Txt size={12} color={colors.muted}>
        {detail}
      </Txt>
    </View>
  );
}

const COLUMNS = ["Investment", "You put in", "Worth today", "Matures", "At maturity"];

/** The wide table of investments, each row opening its detail. */
export function InvestmentsTable({
  investments,
  selectedId,
  onOpen,
}: {
  investments: Investment[];
  selectedId?: string | null;
  onOpen: (investment: Investment) => void;
}) {
  return (
    <View style={styles.table} testID="investments-table">
      <View style={styles.tableRow} accessibilityRole="header">
        {COLUMNS.map((column, index) => (
          <Txt key={column} size={12} weight="bold" color={colors.muted} style={[styles.cell, index === 0 && styles.firstCell]}>
            {column}
          </Txt>
        ))}
      </View>
      {investments.map((investment) => {
        const waiting = investment.status === "awaiting_payment";
        const label = investment.atMaturity ? atMaturityLabel[investment.atMaturity] : "Open term";
        return (
          <Pressable
            key={investment.id}
            accessibilityRole="link"
            accessibilityLabel={`${investment.productName}, worth ${formatAmount(investment.value)}`}
            onPress={() => onOpen(investment)}
            style={({ pressed, hovered }: { pressed: boolean; hovered?: boolean }) => [
              styles.tableRow,
              styles.softTop,
              (selectedId === investment.id || hovered) && { backgroundColor: colors.surfaceSunken },
              pressed && { opacity: 0.8 },
            ]}
            testID={`row-${investment.id}`}
          >
            <Txt size={14} weight="bold" style={[styles.cell, styles.firstCell]}>
              {investment.productName}
            </Txt>
            <Txt size={14} style={[styles.cell, styles.tabular]}>
              {figure(investment.amount)}
            </Txt>
            <Txt size={14} style={[styles.cell, styles.tabular]}>
              {waiting ? "Not started" : figure(investment.value)}
            </Txt>
            <Txt size={14} style={[styles.cell, styles.tabular]}>
              {waiting ? investmentLabel(investment) : investment.maturityDate ? formatDay(investment.maturityDate) : "Open term"}
            </Txt>
            <View style={styles.cell}>
              <View style={[styles.pill, investment.atMaturity === "pay_out" ? styles.pillLime : styles.pillGrey, { alignSelf: "flex-start" }]}>
                <Txt size={12} weight="bold" color={investment.atMaturity === "pay_out" ? colors.limeText : colors.black}>
                  {label}
                </Txt>
              </View>
            </View>
          </Pressable>
        );
      })}
    </View>
  );
}

/** A white panel with a heading, for the wide layouts' side column. */
export function Panel({ title, children, testID }: { title: string; children: ReactNode; testID?: string }) {
  return (
    <View style={[styles.card, { padding: 20, gap: 10 }]} testID={testID}>
      <Txt size={17} weight="extrabold" accessibilityRole="header">
        {title}
      </Txt>
      {children}
    </View>
  );
}

/** One line of a panel: a muted label and a bold figure, with a hairline above. */
export function PanelLine({ label, value }: { label: string; value: string }) {
  return (
    <View style={[styles.between, styles.softTop, { paddingTop: 10, gap: 12 }]}>
      <Txt size={14} color={colors.muted} style={{ flexShrink: 1 }}>
        {label}
      </Txt>
      <Txt size={14} weight="bold" style={{ textAlign: "right" }}>
        {value}
      </Txt>
    </View>
  );
}

/** The product split: one bar in shares, and a label for each. Shares come from the API. */
export function SplitBar({ portfolio, onDark = true }: { portfolio: Portfolio; onDark?: boolean }) {
  const palette = onDark ? [colors.lime, colors.aqua, colors.white, colors.onBlackMuted] : [colors.black, colors.aqua, colors.lime, colors.muted];
  return (
    <View style={{ gap: 8 }} testID="product-split">
      <View style={styles.split} accessible accessibilityLabel={portfolio.products.map((share) => `${share.productName}, ${share.percent}%`).join("; ")}>
        {portfolio.products.map((share, index) =>
          share.percent > 0 ? (
            <View key={share.productCode} style={{ width: `${share.percent}%`, backgroundColor: palette[index % palette.length], borderRadius: 6 }} />
          ) : null,
        )}
      </View>
      <View style={{ flexDirection: "row", flexWrap: "wrap", columnGap: 16, rowGap: 4 }}>
        {portfolio.products.map((share, index) => (
          <View key={share.productCode} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <View style={[styles.legend, { backgroundColor: palette[index % palette.length] }]} />
            <Txt size={12} color={onDark ? colors.onBlackMuted : colors.muted}>
              {`${share.productName}, ${share.percent}%`}
            </Txt>
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  black: { backgroundColor: colors.black, borderRadius: 24, padding: 20, gap: 6 },
  tight: { letterSpacing: -1, fontVariant: ["tabular-nums"] },
  tabular: { fontVariant: ["tabular-nums"] },
  figureRow: { flexDirection: "row", alignItems: "baseline", gap: 8 },
  earning: {
    marginTop: 8,
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.lime,
    borderRadius: 999,
    paddingVertical: 7,
    paddingHorizontal: 12,
  },
  limeButton: {
    marginTop: 8,
    height: 48,
    borderRadius: 24,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
  },
  actions: { flexDirection: "row", gap: 8 },
  action: { flex: 1, alignItems: "center", gap: 6, minHeight: 44 },
  actionCircle: { width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center" },
  actionLime: { backgroundColor: colors.lime },
  actionWhite: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border },
  listCard: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 4,
  },
  holding: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12, minHeight: 44 },
  divider: { borderBottomWidth: 1, borderBottomColor: colors.border },
  softDivider: { borderBottomWidth: 1, borderBottomColor: colors.divider },
  softTop: { borderTopWidth: 1, borderTopColor: colors.divider },
  dot: { width: 10, height: 10, borderRadius: 5 },
  card: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    paddingVertical: 14,
    paddingHorizontal: 16,
  },
  between: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  eyebrow: { textTransform: "uppercase", letterSpacing: 0.6 },
  pill: { borderRadius: 999, paddingVertical: 4, paddingHorizontal: 10 },
  pillGrey: { backgroundColor: colors.surfaceSunken },
  pillLime: { backgroundColor: colors.limeSurface },
  track: { height: 8, borderRadius: 4, backgroundColor: colors.border, flexDirection: "row" },
  bar: { borderRadius: 4, backgroundColor: colors.black },
  step: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 11, minHeight: 44 },
  tile: {
    flexGrow: 1,
    flexBasis: 150,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 18,
    padding: 16,
    gap: 2,
  },
  table: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    overflow: "hidden",
  },
  tableRow: { flexDirection: "row", alignItems: "center", paddingHorizontal: 12, paddingVertical: 12, minHeight: 44 },
  cell: { flex: 1, paddingHorizontal: 6 },
  firstCell: { flex: 1.6 },
  split: { height: 12, borderRadius: 6, flexDirection: "row", gap: 3, overflow: "hidden" },
  legend: { width: 8, height: 8, borderRadius: 4 },
});
