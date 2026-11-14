import { useState } from "react";
import { StyleSheet, View } from "react-native";

import { ordinal } from "@/autoinvest/schedule";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { formatDay } from "@/payments/format";
import { colors } from "@/theme";

import { kampalaToday } from "./amount";
import { useGoals } from "./GoalsProvider";
import { describeGoalError, Goal } from "./types";

/** Undoing a lock waits a week (Solo's rule, docs/goals.md). */
export const UNLOCK_WAIT_DAYS = 7;

export function addDays(day: string, days: number): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, (d ?? 1) + days)).toISOString().slice(0, 10);
}

export type LockState = "none" | "can_lock" | "locked" | "unlocking";

/** Where a goal stands on locking, as its screen shows it. */
export function lockState(goal: Goal, today: string = kampalaToday()): LockState {
  if (goal.lockedUntil) return goal.unlockRequestedAt ? "unlocking" : "locked";
  if (goal.status !== "active" || !goal.targetDate || goal.targetDate <= today) return "none";
  return "can_lock";
}

/** "We remind you on the 25th of each month", or null with no payday. */
export function paydayLine(goal: Goal): string | null {
  return goal.payday ? `We remind you on the ${ordinal(goal.payday)} of each month` : null;
}

/**
 * Lock a goal until its date, or ask to unlock it. Every change is one tap
 * then a confirm, and the goal comes back as it now is.
 */
export function LockCard({ goal, onChanged }: { goal: Goal; onChanged: (goal: Goal) => void }) {
  const client = useGoals();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const state = lockState(goal);
  if (state === "none") return null;

  const run = async (call: () => Promise<Goal>) => {
    setBusy(true);
    setError(null);
    try {
      onChanged(await call());
      setConfirming(false);
    } catch (caught) {
      setError(describeGoalError(caught));
    } finally {
      setBusy(false);
    }
  };
  const lock = () => run(() => client.lockGoal(goal.id));
  const unlock = () => run(() => client.unlockGoal(goal.id));
  const freeOn = addDays(kampalaToday(), UNLOCK_WAIT_DAYS);

  let title: string;
  let body: string;
  if (state === "can_lock") {
    title = `Lock it until ${formatDay(goal.targetDate as string)}`;
    body =
      "Keep your hands off it. Until then, none of this goal's savings can be taken out early or sent to anyone. Undoing a lock takes 7 days.";
  } else if (state === "locked") {
    title = `Locked until ${formatDay(goal.lockedUntil as string)}`;
    body =
      "None of this goal's savings can be taken out early or sent to anyone before then. New savings are locked too.";
  } else {
    title = "Unlocking";
    body = `Your savings are free from ${formatDay(goal.lockedUntil as string)}. Until then they stay locked.`;
  }

  return (
    <View style={styles.card} testID={`goal-lock-${state}`}>
      <View style={styles.head}>
        <View style={[styles.mark, state !== "can_lock" && { backgroundColor: colors.black }]}>
          <Icon name="lock" size={16} strokeWidth={2.2} color={state === "can_lock" ? colors.ink : colors.lime} />
        </View>
        <View style={{ flex: 1, gap: 2 }}>
          <Txt size={15} weight="bold">
            {title}
          </Txt>
          <Txt size={13} color={colors.muted}>
            {body}
          </Txt>
        </View>
      </View>
      {error ? <Notice tone="danger">{error}</Notice> : null}
      {state === "can_lock" ? (
        confirming ? (
          <View style={{ gap: 10 }} testID="lock-confirm">
            <Txt size={13}>
              {`Lock until ${formatDay(goal.targetDate as string)}? If you change your mind, your savings are free 7 days after you ask.`}
            </Txt>
            <Button label="Lock this goal" onPress={lock} loading={busy} height={48} testID="lock-yes" />
            <TextLink label="Not now" onPress={() => setConfirming(false)} underline={false} />
          </View>
        ) : (
          <Button
            label="Lock this goal"
            onPress={() => setConfirming(true)}
            variant="secondary"
            icon="lock"
            height={48}
            testID="goal-lock"
          />
        )
      ) : state === "locked" ? (
        confirming ? (
          <View style={{ gap: 10 }} testID="unlock-confirm">
            <Txt size={13}>
              {`Unlock this goal? Your savings will be free on ${formatDay(freeOn)}, 7 days from now. Until then they stay locked.`}
            </Txt>
            <Button label="Ask to unlock" onPress={unlock} loading={busy} height={48} testID="unlock-yes" />
            <TextLink label="Keep it locked" onPress={() => setConfirming(false)} underline={false} />
          </View>
        ) : (
          <TextLink label="Unlock" onPress={() => setConfirming(true)} testID="goal-unlock" />
        )
      ) : goal.status === "active" ? (
        <TextLink label="Keep it locked instead" onPress={lock} testID="goal-relock" />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 16,
    gap: 12,
  },
  head: { flexDirection: "row", gap: 12, alignItems: "flex-start" },
  mark: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
});
