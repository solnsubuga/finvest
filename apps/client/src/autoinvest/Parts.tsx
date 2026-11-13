import { Pressable, StyleSheet, View } from "react-native";

import { Txt } from "@/components/Txt";
import { formatMoney } from "@/payments/format";
import { colors } from "@/theme";

import { formatDay, scheduleLabel } from "./schedule";
import { Plan, PlanStatus } from "./types";

const statusCopy: Record<PlanStatus, { label: string; background: string; text: string }> = {
  active: {
    label: "On",
    background: colors.limeSurface,
    text: colors.limeText,
  },
  paused: {
    label: "Paused",
    background: colors.warningSurface,
    text: colors.warningText,
  },
  stopped: {
    label: "Stopped",
    background: colors.surfaceSunken,
    text: colors.muted,
  },
};

export function StatusPill({ status }: { status: PlanStatus }) {
  const copy = statusCopy[status];
  return (
    <View style={[styles.pill, { backgroundColor: copy.background }]}>
      <Txt size={12} weight="bold" color={copy.text}>
        {copy.label}
      </Txt>
    </View>
  );
}

/** One plan in the list: how much, how often, into what, and when next. */
export function PlanCard({ plan, onPress }: { plan: Plan; onPress: () => void }) {
  const next = plan.nextRunOn ? `Next on ${formatDay(plan.nextRunOn)}` : "No payments while paused";
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={`${formatMoney(plan.amount)}, ${scheduleLabel(plan.frequency, plan.day)}, ${plan.product.name}. ${next}`}
      onPress={onPress}
      testID={`plan-${plan.id}`}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.75 }]}
    >
      <View style={styles.top}>
        <Txt size={20} weight="extrabold" style={{ letterSpacing: -0.4 }}>
          {formatMoney(plan.amount)}
        </Txt>
        <StatusPill status={plan.status} />
      </View>
      <Txt size={14} weight="semibold">
        {scheduleLabel(plan.frequency, plan.day)}
      </Txt>
      <Txt size={13} color={colors.muted}>
        {`Into the ${plan.product.name}. ${next}.`}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pill: { borderRadius: 12, paddingHorizontal: 10, paddingVertical: 4 },
  card: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 16,
    gap: 4,
  },
  top: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
});
