import { Href, useRouter } from "expo-router";
import { StyleSheet, View } from "react-native";

import { BackButton } from "@/components/BackButton";
import { StepProgress } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

import { stepNumber, Step } from "./steps";
import { DocumentType } from "./types";

/** Back, then "2 of 5": the top row of every step of the ID check. */
export function StepTop({ step, documentType, fallback }: { step: Step; documentType: DocumentType | null; fallback: Href }) {
  const where = stepNumber(step, documentType);
  return (
    <View style={styles.top}>
      <BackButton fallback={fallback} />
      <StepProgress step={where.step} of={where.of} />
    </View>
  );
}

export type Progress = "done" | "now" | "next";

/** One line of the status list: a tick when done, a ring while under way. */
export function StatusLine({ state, title, detail }: { state: Progress; title: string; detail: string }) {
  return (
    <View style={styles.line} accessibilityLabel={`${title}: ${state === "done" ? "done" : state === "now" ? "under way" : "not yet"}`}>
      <View
        style={[
          styles.mark,
          state === "done" && { backgroundColor: colors.black },
          state === "now" && { borderWidth: 3, borderColor: colors.black },
          state === "next" && { borderWidth: 2, borderColor: colors.borderStrong },
        ]}
      >
        {state === "done" ? <Icon name="check" size={14} color={colors.lime} strokeWidth={3} /> : null}
      </View>
      <View style={{ flex: 1 }}>
        <Txt weight={state === "now" ? "bold" : state === "done" ? "semibold" : "medium"} color={state === "next" ? colors.muted : colors.ink}>
          {title}
        </Txt>
        <Txt size={fontSizes.xs} color={colors.muted}>
          {detail}
        </Txt>
      </View>
    </View>
  );
}

/** The round black badge at the top of a status screen. */
export function Badge({ icon, size = 72 }: { icon: "check" | "shield"; size?: number }) {
  return (
    <View style={[styles.badge, { width: size, height: size, borderRadius: size / 2 }]}>
      <Icon name={icon} size={size * 0.46} color={colors.lime} strokeWidth={2.4} />
    </View>
  );
}

/**
 * Shown where something needs an approved client: says why, and opens the ID
 * check. Used on the investing, goals, automatic investing and bank transfer screens.
 */
export function IdCheckPrompt({ message }: { message: string }) {
  const router = useRouter();
  return (
    <View style={styles.prompt} accessibilityRole="alert">
      <Txt size={fontSizes.sm} color={colors.warningText}>
        {message}
      </Txt>
      <Button label="Go to my ID check" variant="secondary" height={48} onPress={() => router.push("/id-check")} testID="open-id-check" />
    </View>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: "row", alignItems: "center", gap: 16 },
  line: { flexDirection: "row", alignItems: "center", gap: 12 },
  mark: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  badge: { backgroundColor: colors.black, alignItems: "center", justifyContent: "center" },
  prompt: { gap: 10, backgroundColor: colors.warningSurface, borderRadius: 14, padding: 14 },
});
