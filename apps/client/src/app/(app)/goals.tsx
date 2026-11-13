import { useCallback, useState } from "react";
import { StyleSheet, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";

import { AppPage } from "@/components/AppPage";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Txt } from "@/components/Txt";
import { GoalCard } from "@/goals/Parts";
import { useGoals } from "@/goals/GoalsProvider";
import { describeGoalError, Goal } from "@/goals/types";
import { Loading } from "@/payments/Parts";
import { colors } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

type State =
  | { status: "loading" }
  | { status: "ready"; goals: Goal[] }
  | { status: "error"; message: string };

export default function Goals() {
  const router = useRouter();
  const client = useGoals();
  const wide = useIsWide();
  const [state, setState] = useState<State>({ status: "loading" });

  const load = useCallback(() => {
    let live = true;
    setState((current) => (current.status === "ready" ? current : { status: "loading" }));
    client.listGoals().then(
      (goals) => live && setState({ status: "ready", goals }),
      (caught) => live && setState({ status: "error", message: describeGoalError(caught) }),
    );
    return () => {
      live = false;
    };
  }, [client]);

  // Read again whenever the page comes back into view, so a new goal or saving shows.
  useFocusEffect(load);

  const newGoal = () => router.push("/goals/new");

  return (
    <AppPage
      title="Goals"
      subtitle={wide ? "Each saving is invested the day it is paid, and earns interest daily." : undefined}
    >
      {state.status === "loading" ? (
        <Loading label="Loading your goals" />
      ) : state.status === "error" ? (
        <View style={{ gap: 12 }}>
          <Notice tone="danger">{state.message}</Notice>
          <Button label="Try again" onPress={load} variant="primary" testID="goals-retry" />
        </View>
      ) : state.goals.length === 0 ? (
        <Empty onStart={newGoal} />
      ) : (
        <>
          <View style={styles.actions}>
            <View style={{ width: wide ? 200 : "100%" }}>
              <Button label="New goal" onPress={newGoal} variant="lime" height={48} testID="new-goal" />
            </View>
          </View>
          <View style={[styles.list, wide && styles.grid]}>
            {state.goals.map((goal) => (
              <View key={goal.id} style={wide ? styles.cell : undefined}>
                <GoalCard goal={goal} onPress={() => router.push(`/goals/${goal.id}`)} />
              </View>
            ))}
          </View>
        </>
      )}
    </AppPage>
  );
}

function Empty({ onStart }: { onStart: () => void }) {
  return (
    <View style={styles.empty} testID="goals-empty">
      <Txt size={20} weight="extrabold" color={colors.white}>
        Save for something that matters
      </Txt>
      <Txt size={14} color={colors.onBlackMuted}>
        School fees, land, a wedding or an emergency fund. Tell us how much and by when, and we work out
        what to save each month. Every saving goes into Treasury bills and earns interest from the next
        day.
      </Txt>
      <View style={{ marginTop: 6 }}>
        <Button label="Start your first goal" onPress={onStart} variant="lime" testID="first-goal" />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: "row", justifyContent: "flex-end" },
  list: { gap: 14 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 16 },
  cell: { flexBasis: "48%", flexGrow: 1, maxWidth: "49%" },
  empty: { backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 8 },
});
