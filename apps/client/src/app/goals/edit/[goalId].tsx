import { useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { groupDigits, kampalaToday, monthYear, parseShillings } from "@/goals/amount";
import { addMonths } from "@/goals/dates";
import { useGoals } from "@/goals/GoalsProvider";
import { CATEGORIES, categoryCopy, describeGoalError, Goal, GoalCategory } from "@/goals/types";
import { Loading } from "@/payments/Parts";
import { colors } from "@/theme";

/** Months from today the client can move the date to, as when the goal was set up. */
const HORIZONS = [6, 12, 18, 24, 36, 60] as const;
type When = "keep" | (typeof HORIZONS)[number] | "none";

function whenLabel(option: When, current: string | null): string {
  if (option === "keep") return current ? `Keep ${monthYear(current)}` : "Keep no date";
  if (option === "none") return "No date";
  if (option % 12 !== 0) return `${option} months`;
  return option === 12 ? "1 year" : `${option / 12} years`;
}

type Loaded =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; goal: Goal };

/**
 * Change a goal: its name, what it is for, the target and the date. Savings already made stay
 * where they are, and the monthly saving is worked out again from the new target and date.
 */
export default function EditGoal() {
  const router = useRouter();
  const client = useGoals();
  const { goalId } = useLocalSearchParams<{ goalId: string }>();
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [category, setCategory] = useState<GoalCategory>("other");
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [when, setWhen] = useState<When>("keep");
  const [fieldError, setFieldError] = useState<{ name?: string; target?: string }>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    client.getGoal(goalId).then(
      (goal) => {
        if (!live) return;
        setLoaded({ status: "ready", goal });
        setCategory(goal.category);
        setName(goal.name);
        setAmount(groupDigits(String(goal.target.minor)));
      },
      (caught) => live && setLoaded({ status: "error", message: describeGoalError(caught) }),
    );
    return () => {
      live = false;
    };
  }, [client, goalId]);

  const goal = loaded.status === "ready" ? loaded.goal : null;

  const save = async () => {
    if (!goal) return;
    const cleaned = name.trim().replace(/\s+/g, " ");
    const target = parseShillings(amount);
    const problems: { name?: string; target?: string } = {};
    if (!cleaned) problems.name = "Give your goal a name.";
    else if (cleaned.length > 60) problems.name = "Keep the name to 60 characters.";
    if (!target) problems.target = "Enter how much you need, in shillings.";
    setFieldError(problems);
    if (problems.name || problems.target || !target) return;
    const targetDate =
      when === "keep" ? goal.targetDate : when === "none" ? null : addMonths(kampalaToday(), when);
    setSaving(true);
    setSaveError(null);
    try {
      await client.updateGoal(goal.id, {
        name: cleaned,
        category,
        target: { currency: goal.target.currency, minor: target },
        targetDate,
      });
      router.back();
    } catch (caught) {
      setSaveError(describeGoalError(caught));
    } finally {
      setSaving(false);
    }
  };

  const options: When[] = ["keep", ...HORIZONS, ...(goal?.targetDate ? (["none"] as const) : [])];

  return (
    <Screen
      signedIn
      footer={
        goal?.status === "active" ? (
          <Button label="Save changes" onPress={save} loading={saving} testID="goal-edit-save" />
        ) : null
      }
    >
      <BackButton fallback={`/goals/${goalId}`} />
      {loaded.status === "loading" ? (
        <Loading label="Loading your goal" />
      ) : loaded.status === "error" ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">{loaded.message}</Notice>
        </View>
      ) : goal && goal.status !== "active" ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="neutral">This goal is closed, so it cannot be changed.</Notice>
        </View>
      ) : goal ? (
        <View style={{ marginTop: 16, gap: 18 }}>
          <View style={{ gap: 6 }}>
            <Title>Change your goal</Title>
            <Txt color={colors.muted}>
              What you have saved stays invested. Your monthly saving is worked out again from the new
              target and date.
            </Txt>
          </View>

          <TextField
            label="Name"
            value={name}
            onChangeText={setName}
            maxLength={80}
            error={fieldError.name ?? null}
            testID="goal-edit-name"
          />

          <View style={{ gap: 8 }}>
            <Txt size={13} weight="bold">
              What it is for
            </Txt>
            <View style={styles.chips} accessibilityRole="radiogroup">
              {CATEGORIES.map((option) => {
                const on = option === category;
                return (
                  <Chip
                    key={option}
                    label={categoryCopy[option].name}
                    on={on}
                    onPress={() => setCategory(option)}
                    testID={`goal-edit-category-${option}`}
                  />
                );
              })}
            </View>
          </View>

          <TextField
            label="How much do you need?"
            hint={`In shillings. You have ${groupDigits(String(goal.progress.value.minor))} so far.`}
            value={amount}
            onChangeText={(text) => setAmount(groupDigits(text))}
            keyboardType="number-pad"
            inputMode="numeric"
            error={fieldError.target ?? null}
            testID="goal-edit-target"
          />

          <View style={{ gap: 8 }}>
            <Txt size={13} weight="bold">
              By when?
            </Txt>
            <View style={styles.chips} accessibilityRole="radiogroup">
              {options.map((option) => (
                <Chip
                  key={String(option)}
                  label={whenLabel(option, goal.targetDate)}
                  on={option === when}
                  onPress={() => setWhen(option)}
                  testID={`goal-edit-when-${option}`}
                />
              ))}
            </View>
            <Txt size={13} color={colors.muted}>
              {when === "keep"
                ? goal.targetDate
                  ? `You need it in ${monthYear(goal.targetDate)}.`
                  : "No deadline. Save when you can."
                : when === "none"
                  ? "No deadline. Save when you can, and the money stays close to hand in the shortest bill."
                  : `You need it in ${monthYear(addMonths(kampalaToday(), when))}.`}
            </Txt>
          </View>

          {saveError ? <Notice tone="danger">{saveError}</Notice> : null}
        </View>
      ) : null}
    </Screen>
  );
}

function Chip({
  label,
  on,
  onPress,
  testID,
}: {
  label: string;
  on: boolean;
  onPress: () => void;
  testID: string;
}) {
  return (
    <Pressable
      accessibilityRole="radio"
      aria-checked={on}
      onPress={onPress}
      style={[styles.chip, on && styles.chipOn]}
      testID={testID}
    >
      <Txt size={13} weight="bold" color={on ? colors.white : colors.ink}>
        {label}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    minHeight: 44,
    paddingHorizontal: 14,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    justifyContent: "center",
  },
  chipOn: { backgroundColor: colors.black, borderColor: colors.black },
});
