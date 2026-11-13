import { useCallback, useState } from "react";
import { StyleSheet, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";

import { AppPage } from "@/components/AppPage";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Txt } from "@/components/Txt";
import { GoalCard } from "@/goals/Parts";
import { GroupCard } from "@/goals/GroupParts";
import { Group } from "@/goals/groups";
import { useGoals, useGroups } from "@/goals/GoalsProvider";
import { describeGoalError, Goal } from "@/goals/types";
import { Loading } from "@/payments/Parts";
import { colors } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

type State =
  | { status: "loading" }
  // `groups` is null when only the group goals could not be loaded.
  | { status: "ready"; goals: Goal[]; groups: Group[] | null }
  | { status: "error"; message: string };

export default function Goals() {
  const router = useRouter();
  const client = useGoals();
  const groupsClient = useGroups();
  const wide = useIsWide();
  const [state, setState] = useState<State>({ status: "loading" });

  const load = useCallback(() => {
    let live = true;
    setState((current) => (current.status === "ready" ? current : { status: "loading" }));
    Promise.all([client.listGoals(), groupsClient.listGroups().catch(() => null)]).then(
      ([goals, groups]) => {
        // A member's own part of a group is shown in the group, not twice.
        const parts = new Set((groups ?? []).map((group) => group.ownGoalId));
        live && setState({ status: "ready", goals: goals.filter((goal) => !parts.has(goal.id)), groups });
      },
      (caught) => live && setState({ status: "error", message: describeGoalError(caught) }),
    );
    return () => {
      live = false;
    };
  }, [client, groupsClient]);

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
      ) : state.goals.length === 0 && (state.groups ?? []).length === 0 ? (
        <>
          <Empty onStart={newGoal} />
          <Together
            groups={state.groups}
            wide={wide}
            onOpen={(id) => router.push(`/goals/groups/${id}`)}
            onStart={() => router.push("/goals/groups/new")}
            onJoin={() => router.push("/goals/groups/join")}
          />
        </>
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
          <Together
            groups={state.groups}
            wide={wide}
            onOpen={(id) => router.push(`/goals/groups/${id}`)}
            onStart={() => router.push("/goals/groups/new")}
            onJoin={() => router.push("/goals/groups/join")}
          />
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

/** Group goals: one target shared with family or friends, each saving into their own goal. */
function Together({
  groups,
  wide,
  onOpen,
  onStart,
  onJoin,
}: {
  groups: Group[] | null;
  wide: boolean;
  onOpen: (groupId: string) => void;
  onStart: () => void;
  onJoin: () => void;
}) {
  return (
    <View style={{ gap: 12, marginTop: 10 }} testID="group-goals">
      <View style={{ gap: 4 }}>
        <Txt size={17} weight="extrabold">
          Saving together
        </Txt>
        <Txt size={13} color={colors.muted}>
          One target with family or friends. Each of you saves into your own goal, and nobody else
          can touch your money.
        </Txt>
      </View>
      {groups === null ? (
        <Notice tone="warning">Your group goals could not be loaded. Your own goals are above.</Notice>
      ) : groups.length > 0 ? (
        <View style={[styles.list, wide && styles.grid]}>
          {groups.map((group) => (
            <View key={group.id} style={wide ? styles.cell : undefined}>
              <GroupCard group={group} onPress={() => onOpen(group.id)} />
            </View>
          ))}
        </View>
      ) : null}
      <View style={[styles.together, wide && { flexDirection: "row" }]}>
        <View style={wide ? { flex: 1 } : undefined}>
          <Button label="Start a group goal" onPress={onStart} variant="primary" height={48} testID="new-group" />
        </View>
        <View style={wide ? { flex: 1 } : undefined}>
          <Button label="Join with a link or code" onPress={onJoin} variant="secondary" height={48} testID="join-group" />
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  together: { gap: 10 },
  actions: { flexDirection: "row", justifyContent: "flex-end" },
  list: { gap: 14 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 16 },
  cell: { flexBasis: "48%", flexGrow: 1, maxWidth: "49%" },
  empty: { backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 8 },
});
