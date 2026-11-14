import { Pressable, StyleSheet, View } from "react-native";

import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { formatDay, formatMoney } from "@/payments/format";
import { colors, fontSizes, radii } from "@/theme";

import { Goal, Plan, Standing, standingCopy } from "./types";

const standingTones: Record<Standing, { background: string; text: string }> = {
  reached: { background: colors.lime, text: colors.ink },
  on_track: { background: colors.limeSurface, text: colors.limeText },
  behind: { background: colors.warningSurface, text: colors.warningText },
  no_date: { background: colors.surfaceSunken, text: colors.black },
};

/** "On track", "Behind", or the percent for a goal with no date. */
export function StandingBadge({ goal, onDark = false }: { goal: Goal; onDark?: boolean }) {
  const { standing, percent } = goal.progress;
  const tone = onDark && standing === "on_track" ? standingTones.reached : standingTones[standing];
  const label = standing === "no_date" ? `${percent}%` : standingCopy[standing];
  return (
    <View style={[styles.badge, { backgroundColor: tone.background }]}>
      <Txt size={12} weight="bold" color={tone.text}>
        {label}
      </Txt>
    </View>
  );
}

/** How far along a goal is. Saved money in black, interest in lime. */
export function ProgressBar({ goal, onDark = false }: { goal: Goal; onDark?: boolean }) {
  const target = goal.target.minor;
  const value = Math.min(goal.progress.value.minor, target);
  const interest = Math.min(goal.progress.interest.minor, value);
  const saved = ((value - interest) / target) * 100;
  const earned = (interest / target) * 100;
  return (
    <View
      style={[styles.track, { backgroundColor: onDark ? colors.onBlackDivider : colors.border }]}
      accessibilityRole="progressbar"
      accessibilityLabel={`${goal.progress.percent}% of the target`}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={goal.progress.percent}
    >
      <View
        style={[
          styles.fill,
          { width: `${saved}%`, backgroundColor: onDark ? colors.white : colors.black },
        ]}
      />
      <View style={[styles.fill, { width: `${earned}%`, backgroundColor: colors.lime }]} />
    </View>
  );
}

export function goalSubtitle(goal: Goal): string {
  if (goal.status === "closed") {
    return "Closed. Its investments keep running.";
  }
  return goal.targetDate
    ? `Needed by ${formatDay(goal.targetDate)}`
    : "No deadline, earns interest daily";
}

/** One goal in the list, as on the canvas. */
export function GoalCard({ goal, onPress }: { goal: Goal; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={`${goal.name}, ${formatMoney(goal.progress.value)} of ${formatMoney(goal.target)}`}
      onPress={onPress}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.8 }]}
      testID={`goal-${goal.id}`}
    >
      <View style={styles.head}>
        <View style={{ flex: 1 }}>
          <Txt size={17} weight="extrabold">
            {goal.name}
          </Txt>
          <Txt size={13} color={colors.muted}>
            {goalSubtitle(goal)}
          </Txt>
        </View>
        <StandingBadge goal={goal} />
      </View>
      <View style={styles.amounts}>
        <Txt size={26} weight="extrabold" lineHeight={32} style={{ letterSpacing: -0.5 }}>
          {formatMoney(goal.progress.value)}
        </Txt>
        <Txt size={13} color={colors.muted}>
          {`of ${formatMoney(goal.target).replace("UGX ", "")}`}
        </Txt>
      </View>
      <ProgressBar goal={goal} />
      <View style={styles.next}>
        <Icon name={goal.plan ? "plus" : "goals"} size={16} strokeWidth={2.2} />
        <Txt size={13} style={{ flex: 1 }}>
          {nextLine(goal)}
        </Txt>
      </View>
    </Pressable>
  );
}

function nextLine(goal: Goal): string {
  if (goal.status === "closed") {
    return "Nothing more is saved to a closed goal";
  }
  if (goal.progress.standing === "reached") {
    return "Target reached. Your money keeps earning until you need it";
  }
  if (goal.progress.awaitingPayment.minor > 0) {
    return `${formatMoney(goal.progress.awaitingPayment)} waiting for your payment`;
  }
  return goal.plan
    ? `Save ${formatMoney(goal.plan.saving)} a month to get there`
    : "Save any amount from UGX 100,000";
}

/** The black card with the monthly saving and what interest adds. */
export function PlanCard({ plan, target }: { plan: Plan; target: number }) {
  const putShare = Math.min(100, (plan.putIn.minor / Math.max(target, 1)) * 100);
  return (
    <View style={styles.plan} testID="plan-card">
      <Txt size={13} color={colors.onBlackMuted}>
        To get there, save
      </Txt>
      <View style={styles.amounts}>
        <Txt size={34} weight="extrabold" lineHeight={40} color={colors.white} style={{ letterSpacing: -0.8 }}>
          {formatMoney(plan.saving)}
        </Txt>
        <Txt size={14} color={colors.onBlackMuted}>
          a month
        </Txt>
      </View>
      <View style={styles.split}>
        <View style={[styles.splitPart, { flexBasis: `${putShare}%`, backgroundColor: colors.white }]} />
        <View style={[styles.splitPart, { flex: 1, backgroundColor: colors.lime }]} />
      </View>
      <View style={styles.splitLabels}>
        <Txt size={13} color={colors.white}>
          {`You put in ${formatMoney(plan.putIn)}`}
        </Txt>
        <Txt size={13} weight="bold" color={colors.lime}>
          {`Interest adds about ${formatMoney(plan.interest)}`}
        </Txt>
      </View>
      <Txt size={12} color={colors.onBlackMuted}>
        {plan.raisedToMinimum
          ? `Each saving is at least ${formatMoney(plan.saving)}, the smallest amount the bill takes, so you get there early.`
          : `Without investing you would need ${formatMoney(plan.withoutInvesting)} a month.`}
      </Txt>
    </View>
  );
}

/** Where the plan invests, and why that bill. */
export function PlanProductNote({ plan }: { plan: Plan }) {
  return (
    <View style={styles.note}>
      <Txt size={fontSizes.xs} weight="bold" color={colors.muted} style={styles.eyebrow}>
        How your money will work
      </Txt>
      <Txt size={15} weight="extrabold">
        {`${plan.productName}, ${plan.ratePercent}% a year after tax`}
      </Txt>
      <Txt size={13} color={colors.muted}>
        {`${plan.savings} monthly savings from ${formatDay(plan.firstOn)} to ${formatDay(plan.lastOn)}. Each one is invested the day it is paid and rolls over, and the last one matures before you need the money.`}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: { borderRadius: 999, paddingHorizontal: 10, paddingVertical: 4, alignSelf: "flex-start" },
  track: { height: 10, borderRadius: 5, flexDirection: "row", overflow: "hidden" },
  fill: { height: "100%" },
  card: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 22,
    padding: 18,
    gap: 12,
  },
  head: { flexDirection: "row", alignItems: "flex-start", gap: 12 },
  amounts: { flexDirection: "row", alignItems: "baseline", gap: 6, flexWrap: "wrap" },
  next: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: colors.paper,
    borderRadius: 12,
    paddingVertical: 10,
    paddingHorizontal: 12,
  },
  plan: { backgroundColor: colors.black, borderRadius: radii.xl, padding: 18, gap: 10 },
  split: { height: 14, flexDirection: "row", gap: 3 },
  splitPart: { height: 14, borderRadius: 7 },
  splitLabels: { flexDirection: "row", justifyContent: "space-between", flexWrap: "wrap", gap: 6 },
  note: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    paddingVertical: 14,
    paddingHorizontal: 16,
    gap: 6,
  },
  eyebrow: { textTransform: "uppercase", letterSpacing: 0.6 },
});
