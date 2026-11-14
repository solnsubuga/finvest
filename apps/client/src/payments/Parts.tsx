import { ActivityIndicator, StyleSheet, View } from "react-native";

import { Card } from "@/components/Blocks";
import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors, fontSizes, radii } from "@/theme";

import { formatMoney } from "./format";
import { Money } from "./types";

/** The black card that says how much and for what. */
export function AmountCard({ title, amount, note }: { title: string; amount: Money; note: string }) {
  return (
    <View style={styles.amountCard}>
      <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
        {title}
      </Txt>
      <Txt size={34} weight="extrabold" lineHeight={40} color={colors.white} style={{ letterSpacing: -0.8 }}>
        {formatMoney(amount)}
      </Txt>
      <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
        {note}
      </Txt>
    </View>
  );
}

export type Detail = { label: string; value: string };

/** Label on the left, value on the right, one fact per row. */
export function DetailList({ rows }: { rows: Detail[] }) {
  return (
    <Card padded={false}>
      {rows.map((row, index) => (
        <View
          key={row.label}
          style={[styles.detailRow, index > 0 && styles.detailDivider]}
          accessible
          accessibilityLabel={`${row.label}: ${row.value}`}
        >
          <Txt size={fontSizes.sm} color={colors.muted}>
            {row.label}
          </Txt>
          <Txt size={fontSizes.md} weight="semibold" style={styles.detailValue}>
            {row.value}
          </Txt>
        </View>
      ))}
    </Card>
  );
}

export type Step = { label: string; state: "done" | "now" | "later"; note?: string };

const stepWords = { done: "done", now: "in progress", later: "not yet" };

/** The steps of a payment, top to bottom, with the current one marked. */
export function Timeline({ steps }: { steps: Step[] }) {
  return (
    <Card>
      <View style={{ gap: 16 }}>
        {steps.map((step) => (
          <View
            key={step.label}
            style={styles.step}
            accessible
            accessibilityLabel={`${step.label}: ${stepWords[step.state]}`}
          >
            <View
              style={[
                styles.dot,
                step.state === "done" && { backgroundColor: colors.lime },
                step.state === "now" && { backgroundColor: colors.black },
              ]}
            >
              {step.state === "done" ? (
                <Icon name="check" size={14} color={colors.ink} strokeWidth={3} />
              ) : step.state === "now" ? (
                <ActivityIndicator size="small" color={colors.white} style={styles.spinner} aria-hidden />
              ) : null}
            </View>
            <Txt
              size={fontSizes.md}
              weight={step.state === "later" ? "regular" : "bold"}
              color={step.state === "later" ? colors.muted : colors.ink}
              style={{ flex: 1 }}
            >
              {step.label}
            </Txt>
            {step.note ? (
              <Txt size={fontSizes.sm} color={colors.muted}>
                {step.note}
              </Txt>
            ) : null}
          </View>
        ))}
      </View>
    </Card>
  );
}

/** Shown while the first answer is on its way. */
export function Loading({ label }: { label: string }) {
  return (
    <View style={styles.loading}>
      <ActivityIndicator accessibilityLabel={label} color={colors.black} />
    </View>
  );
}

const styles = StyleSheet.create({
  amountCard: {
    backgroundColor: colors.black,
    borderRadius: radii.xl,
    paddingVertical: 20,
    paddingHorizontal: 20,
    gap: 4,
  },
  detailRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 16,
    paddingVertical: 14,
    paddingHorizontal: 18,
  },
  detailDivider: { borderTopWidth: 1, borderTopColor: colors.divider },
  detailValue: { flex: 1, textAlign: "right" },
  step: { flexDirection: "row", alignItems: "center", gap: 12 },
  dot: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  spinner: { transform: [{ scale: 0.6 }] },
  loading: { paddingVertical: 80, alignItems: "center" },
});
