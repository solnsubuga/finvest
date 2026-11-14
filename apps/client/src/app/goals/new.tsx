import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { StepProgress } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { CATEGORIES, categoryCopy, GoalCategory } from "@/goals/types";
import { colors } from "@/theme";

/** Step 1 of 3: what the client is saving for, and a name. */
export default function NewGoal() {
  const router = useRouter();
  const [category, setCategory] = useState<GoalCategory | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const choose = (chosen: GoalCategory) => {
    setCategory(chosen);
    // Suggest the category as the name until the client types their own.
    if (!name || CATEGORIES.some((c) => categoryCopy[c].name === name)) {
      setName(chosen === "other" ? "" : categoryCopy[chosen].name);
    }
  };

  const next = () => {
    const cleaned = name.trim().replace(/\s+/g, " ");
    if (!category) {
      setError("Choose what you are saving for.");
      return;
    }
    if (!cleaned) {
      setError("Give your goal a name.");
      return;
    }
    if (cleaned.length > 60) {
      setError("Keep the name to 60 characters.");
      return;
    }
    setError(null);
    router.push({ pathname: "/goals/plan", params: { category, name: cleaned } });
  };

  return (
    <Screen signedIn footer={<Button label="Continue" onPress={next} testID="goal-continue" />}>
      <View style={styles.top}>
        <BackButton fallback="/goals" />
        <StepProgress step={1} of={3} />
      </View>
      <View style={{ marginTop: 24, gap: 8 }}>
        <Title>What are you saving for?</Title>
        <Txt color={colors.muted}>Pick one and we will build the plan with you.</Txt>
      </View>
      <View style={styles.grid} accessibilityRole="radiogroup">
        {CATEGORIES.map((option) => {
          const copy = categoryCopy[option];
          const on = option === category;
          return (
            <Pressable
              key={option}
              accessibilityRole="radio"
              aria-checked={on}
              accessibilityLabel={`${copy.name}. ${copy.hint}`}
              onPress={() => choose(option)}
              style={[styles.tile, on && styles.tileOn]}
              testID={`category-${option}`}
            >
              <View style={[styles.mark, { backgroundColor: on ? colors.lime : colors.surfaceSunken }]}>
                <Txt size={13} weight="extrabold">
                  {copy.mark}
                </Txt>
              </View>
              <View>
                <Txt size={14} weight="bold">
                  {copy.name}
                </Txt>
                <Txt size={12} color={colors.muted}>
                  {copy.hint}
                </Txt>
              </View>
            </Pressable>
          );
        })}
      </View>
      <View style={{ marginTop: 20 }}>
        <TextField
          label="Give it a name"
          value={name}
          onChangeText={setName}
          placeholder="Plot of land in Wakiso"
          maxLength={80}
          error={error}
          testID="goal-name"
        />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: "row", alignItems: "center", gap: 16 },
  grid: { marginTop: 18, flexDirection: "row", flexWrap: "wrap", gap: 10 },
  tile: {
    flexBasis: "47%",
    flexGrow: 1,
    minHeight: 92,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 18,
    paddingVertical: 12,
    paddingHorizontal: 14,
    justifyContent: "space-between",
    gap: 8,
  },
  tileOn: { borderWidth: 2, borderColor: colors.black },
  mark: { width: 32, height: 32, borderRadius: 10, alignItems: "center", justifyContent: "center" },
});
