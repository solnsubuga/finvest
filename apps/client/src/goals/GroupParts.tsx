import { Pressable, StyleSheet, View } from "react-native";

import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { formatDay, formatMoney } from "@/payments/format";
import { colors } from "@/theme";

import { Group, memberSummary } from "./groups";
import { Standing, standingCopy } from "./types";

const standingTones: Record<Standing, { background: string; text: string }> = {
  reached: { background: colors.lime, text: colors.ink },
  on_track: { background: colors.limeSurface, text: colors.limeText },
  behind: { background: colors.warningSurface, text: colors.warningText },
  no_date: { background: colors.surfaceSunken, text: colors.black },
};

export function GroupStanding({ group, onDark = false }: { group: Group; onDark?: boolean }) {
  const tone = onDark && group.standing === "on_track" ? standingTones.reached : standingTones[group.standing];
  const label = group.status === "closed" ? "Closed" : group.standing === "no_date" ? `${group.percent}%` : standingCopy[group.standing];
  return (
    <View style={[styles.badge, { backgroundColor: group.status === "closed" ? colors.surfaceSunken : tone.background }]}>
      <Txt size={12} weight="bold" color={group.status === "closed" ? colors.black : tone.text}>
        {label}
      </Txt>
    </View>
  );
}

/** How far the members are together. One colour: the group does not split saved from earned. */
export function GroupBar({ group, onDark = false }: { group: Group; onDark?: boolean }) {
  return (
    <View
      style={[styles.track, { backgroundColor: onDark ? colors.onBlackDivider : colors.border }]}
      accessibilityRole="progressbar"
      accessibilityLabel={`${group.percent}% of the group's target`}
      accessibilityValue={{ min: 0, max: 100, now: group.percent }}
    >
      <View style={[styles.fill, { width: `${group.percent}%`, backgroundColor: onDark ? colors.lime : colors.black }]} />
    </View>
  );
}

export function groupSubtitle(group: Group): string {
  if (group.status === "closed") return "Closed. Everyone's goal carries on as their own.";
  return group.targetDate ? `Needed by ${formatDay(group.targetDate)}` : "No deadline";
}

/** One group goal in the list. */
export function GroupCard({ group, onPress }: { group: Group; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={`${group.name}, group goal, ${formatMoney(group.value)} of ${formatMoney(group.target)} together`}
      onPress={onPress}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.8 }]}
      testID={`group-${group.id}`}
    >
      <View style={styles.head}>
        <View style={{ flex: 1 }}>
          <Txt size={17} weight="extrabold">
            {group.name}
          </Txt>
          <Txt size={13} color={colors.muted}>
            {groupSubtitle(group)}
          </Txt>
        </View>
        <GroupStanding group={group} />
      </View>
      <View style={styles.amounts}>
        <Txt size={26} weight="extrabold" lineHeight={32} style={{ letterSpacing: -0.5 }}>
          {formatMoney(group.value)}
        </Txt>
        <Txt size={13} color={colors.muted}>
          {`of ${formatMoney(group.target).replace("UGX ", "")} together`}
        </Txt>
      </View>
      <GroupBar group={group} />
      <View style={styles.next}>
        <Icon name="goals" size={16} strokeWidth={2.2} />
        <Txt size={13} style={{ flex: 1 }}>
          {memberSummary(group.members)}
        </Txt>
      </View>
    </Pressable>
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
});
