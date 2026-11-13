import { ReactNode } from "react";
import { StyleSheet, View } from "react-native";

import { colors, fontSizes, radii } from "@/theme";

import { Icon } from "./Icon";
import { Txt } from "./Txt";

/** White rounded card with a hairline border. */
export function Card({ children, padded = true }: { children: ReactNode; padded?: boolean }) {
  return <View style={[styles.card, padded && { padding: 18 }]}>{children}</View>;
}

type NoticeProps = { tone: "warning" | "danger" | "neutral"; children: string };

const tones = {
  warning: { background: colors.warningSurface, text: colors.warningText },
  danger: { background: colors.dangerSurface, text: colors.danger },
  neutral: { background: colors.surfaceSunken, text: colors.ink },
};

export function Notice({ tone, children }: NoticeProps) {
  const palette = tones[tone];
  return (
    <View
      style={[styles.notice, { backgroundColor: palette.background }]}
      accessibilityRole={tone === "danger" ? "alert" : undefined}
      accessibilityLiveRegion={tone === "danger" ? "assertive" : undefined}
    >
      <Txt size={fontSizes.sm} weight={tone === "danger" ? "semibold" : "regular"} color={palette.text}>
        {children}
      </Txt>
    </View>
  );
}

/** "1 of 4" bar at the top of the sign-up steps. */
export function StepProgress({ step, of }: { step: number; of: number }) {
  return (
    <View
      style={styles.progress}
      accessibilityRole="progressbar"
      accessibilityLabel={`Step ${step} of ${of}`}
      accessibilityValue={{ min: 0, max: of, now: step }}
    >
      <View style={styles.bars}>
        {Array.from({ length: of }, (_, index) => (
          <View
            key={index}
            style={[styles.bar, { backgroundColor: index < step ? colors.black : colors.borderStrong }]}
          />
        ))}
      </View>
      <Txt size={fontSizes.sm} weight="semibold" color={colors.muted}>
        {`${step} of ${of}`}
      </Txt>
    </View>
  );
}

/** A requirement with a tick that fills in once it is met. */
export function CheckRow({ met, children }: { met: boolean; children: string }) {
  return (
    <View
      style={styles.checkRow}
      accessibilityLabel={`${children}: ${met ? "met" : "not met yet"}`}
    >
      <View style={[styles.tick, { backgroundColor: met ? colors.lime : colors.border }]}>
        {met ? <Icon name="check" size={14} color={colors.ink} strokeWidth={3} /> : null}
      </View>
      <Txt size={14} style={{ flex: 1 }}>
        {children}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.xl,
  },
  notice: { borderRadius: radii.lg, paddingVertical: 12, paddingHorizontal: 14 },
  progress: { flex: 1, flexDirection: "row", alignItems: "center", gap: 16 },
  bars: { flex: 1, flexDirection: "row", gap: 6 },
  bar: { flex: 1, height: 6, borderRadius: 3 },
  checkRow: { flexDirection: "row", alignItems: "center", gap: 10, minHeight: 28 },
  tick: { width: 20, height: 20, borderRadius: 10, alignItems: "center", justifyContent: "center" },
});
