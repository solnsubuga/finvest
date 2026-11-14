import { StyleSheet, View } from "react-native";

import { Txt } from "@/components/Txt";
import { colors, fontSizes, radii } from "@/theme";

import { Organisation } from "./types";

export type Tone = "lime" | "grey" | "amber" | "red";

/** Where an application stands, in a few words, and the pill's colours. */
export function standingOf(found: Organisation): { label: string; tone: Tone } {
  if (found.invited) return { label: "Asked to sign", tone: "amber" };
  switch (found.status) {
    case "approved":
      return { label: "Open", tone: "lime" };
    case "rejected":
      return { label: "Not approved", tone: "red" };
    case "submitted":
    case "checked":
      return { label: "With Finvest", tone: "grey" };
    default:
      return found.returnNote ? { label: "Change asked", tone: "amber" } : { label: "Draft", tone: "grey" };
  }
}

export function Pill({ label, tone }: { label: string; tone: Tone }) {
  const palette = {
    lime: { background: colors.limeSurface, text: colors.limeText },
    grey: { background: colors.surfaceSunken, text: colors.ink },
    amber: { background: colors.warningSurface, text: colors.warningText },
    red: { background: colors.dangerSurface, text: colors.danger },
  }[tone];
  return (
    <View style={[styles.pill, { backgroundColor: palette.background }]}>
      <Txt size={fontSizes.xs} weight="bold" color={palette.text}>
        {label}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: { borderRadius: radii.pill, paddingHorizontal: 10, paddingVertical: 4, alignSelf: "center" },
});
