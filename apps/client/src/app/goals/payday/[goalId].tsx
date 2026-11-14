import { useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { useGoals } from "@/goals/GoalsProvider";
import { paydayLine } from "@/goals/Keep";
import { describeGoalError, Goal } from "@/goals/types";
import { Loading } from "@/payments/Parts";
import { colors } from "@/theme";

const DAYS = Array.from({ length: 31 }, (_, index) => index + 1);

type Loaded =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; goal: Goal };

/**
 * Name the day the client is paid. That morning they are reminded to save towards the goal,
 * in place of the reminder before the saving day. Nothing is collected on it.
 */
export default function GoalPayday() {
  const router = useRouter();
  const client = useGoals();
  const { goalId } = useLocalSearchParams<{ goalId: string }>();
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [day, setDay] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    client.getGoal(goalId).then(
      (goal) => {
        if (!live) return;
        setLoaded({ status: "ready", goal });
        setDay(goal.payday);
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
    setSaving(true);
    setSaveError(null);
    try {
      await client.setPayday(goal.id, day);
      router.back();
    } catch (caught) {
      setSaveError(describeGoalError(caught));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        goal?.status === "active" ? (
          <Button
            label={day === null ? "Save, no reminder" : "Save payday"}
            onPress={save}
            loading={saving}
            testID="payday-save"
          />
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
          <Notice tone="neutral">This goal is closed, so it has no reminders.</Notice>
        </View>
      ) : goal ? (
        <View style={{ marginTop: 16, gap: 18 }}>
          <View style={{ gap: 6 }}>
            <Title>When are you paid?</Title>
            <Txt color={colors.muted}>
              {`On the morning you are paid, we remind you to save towards ${goal.name}. Nothing is taken from you: you choose what to save.`}
            </Txt>
          </View>

          <View style={styles.grid} accessibilityRole="radiogroup" accessibilityLabel="Day of the month">
            {DAYS.map((option) => {
              const on = option === day;
              return (
                <Pressable
                  key={option}
                  accessibilityRole="radio"
                  aria-checked={on}
                  accessibilityLabel={`The ${option}`}
                  onPress={() => setDay(option)}
                  style={[styles.day, on && styles.on]}
                  testID={`payday-${option}`}
                >
                  <Txt size={14} weight="bold" color={on ? colors.white : colors.ink}>
                    {String(option)}
                  </Txt>
                </Pressable>
              );
            })}
          </View>
          <Pressable
            accessibilityRole="radio"
            aria-checked={day === null}
            onPress={() => setDay(null)}
            style={[styles.none, day === null && styles.on]}
            testID="payday-none"
          >
            <Txt size={13} weight="bold" color={day === null ? colors.white : colors.ink}>
              No payday reminder
            </Txt>
          </Pressable>

          <Txt size={13} color={colors.muted}>
            {day === null
              ? "You are reminded a few days before your saving day instead."
              : `${paydayLine({ ...goal, payday: day })}. A day past the end of a shorter month falls on its last day.`}
          </Txt>

          {saveError ? <Notice tone="danger">{saveError}</Notice> : null}
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 8, maxWidth: 420 },
  day: {
    width: 48,
    height: 48,
    borderRadius: 24,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  none: {
    alignSelf: "flex-start",
    minHeight: 44,
    paddingHorizontal: 16,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    justifyContent: "center",
  },
  on: { backgroundColor: colors.black, borderColor: colors.black },
});
