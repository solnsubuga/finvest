import { useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice, StepProgress } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { groupDigits, kampalaToday, monthYear, parseShillings } from "@/goals/amount";
import { useGoals } from "@/goals/GoalsProvider";
import { PlanCard, PlanProductNote } from "@/goals/Parts";
import { addMonths } from "@/goals/dates";
import { describeGoalError, Plan } from "@/goals/types";
import { Loading } from "@/payments/Parts";
import { colors } from "@/theme";

/** Months from today the client can choose, or no date at all. */
const HORIZONS = [6, 12, 18, 24, 36, 60] as const;
type Horizon = (typeof HORIZONS)[number] | "none";

function horizonLabel(option: Horizon): string {
  if (option === "none") {
    return "No date";
  }
  if (option % 12 !== 0) {
    return `${option} months`;
  }
  return option === 12 ? "1 year" : `${option / 12} years`;
}

type PlanState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; plan: Plan | null }
  | { status: "error"; message: string };

/** Step 2 of 3: how much, by when, and the monthly saving that gets there. */
export default function GoalPlanScreen() {
  const router = useRouter();
  const client = useGoals();
  const params = useLocalSearchParams<{ category: string; name: string }>();
  const [amount, setAmount] = useState("");
  const [horizon, setHorizon] = useState<Horizon>(12);
  // The last answer, with the question it answers. Anything else is still loading.
  const [answer, setAnswer] = useState<{ query: string; state: PlanState } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const target = parseShillings(amount);
  const targetDate = horizon === "none" ? null : addMonths(kampalaToday(), horizon);

  const query = target && targetDate ? `${target}:${targetDate}` : null;
  const planState: PlanState =
    query === null
      ? { status: "idle" }
      : answer?.query === query
        ? answer.state
        : { status: "loading" };

  useEffect(() => {
    if (!query || !target || !targetDate) {
      return;
    }
    let live = true;
    // Wait for typing to pause before asking for a plan.
    const timer = setTimeout(() => {
      client.getPlan({ currency: "UGX", minor: target }, targetDate).then(
        (plan) => live && setAnswer({ query, state: { status: "ready", plan } }),
        (caught) =>
          live && setAnswer({ query, state: { status: "error", message: describeGoalError(caught) } }),
      );
    }, 350);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [client, query, target, targetDate]);

  const next = () => {
    if (!target) {
      setError("Enter how much you need, in shillings.");
      return;
    }
    setError(null);
    router.push({
      pathname: "/goals/start",
      params: {
        category: params.category,
        name: params.name,
        target: String(target),
        targetDate: targetDate ?? "",
        saving: planState.status === "ready" && planState.plan ? String(planState.plan.saving.minor) : "",
      },
    });
  };

  const tooSoon = planState.status === "ready" && planState.plan === null;

  return (
    <Screen
      signedIn
      footer={
        <Button
          label="Next, choose how to save"
          onPress={next}
          disabled={tooSoon}
          testID="plan-next"
        />
      }
    >
      <View style={styles.top}>
        <BackButton fallback="/goals/new" />
        <StepProgress step={2} of={3} />
      </View>
      <View style={{ marginTop: 22 }}>
        <Title>{params.name ?? "Your goal"}</Title>
      </View>
      <View style={{ marginTop: 16, gap: 16 }}>
        <TextField
          label="How much do you need?"
          hint="In shillings"
          value={amount}
          onChangeText={(text) => setAmount(groupDigits(text))}
          keyboardType="number-pad"
          inputMode="numeric"
          placeholder="2,000,000"
          error={error}
          testID="goal-target"
        />
        <View style={{ gap: 8 }}>
          <Txt size={13} weight="bold">
            By when?
          </Txt>
          <View style={styles.chips} accessibilityRole="radiogroup">
            {[...HORIZONS, "none" as const].map((option) => {
              const on = option === horizon;
              const label = horizonLabel(option);
              return (
                <Pressable
                  key={String(option)}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: on }}
                  onPress={() => setHorizon(option)}
                  style={[styles.chip, on && styles.chipOn]}
                  testID={`horizon-${option}`}
                >
                  <Txt size={13} weight="bold" color={on ? colors.white : colors.ink}>
                    {label}
                  </Txt>
                </Pressable>
              );
            })}
          </View>
          <Txt size={13} color={colors.muted}>
            {targetDate
              ? `You need it in ${monthYear(targetDate)}.`
              : "No deadline. Save when you can, and the money stays close to hand in the shortest bill."}
          </Txt>
        </View>

        {planState.status === "loading" ? (
          <Loading label="Working out your plan" />
        ) : planState.status === "error" ? (
          <Notice tone="danger">{planState.message}</Notice>
        ) : planState.status === "ready" && planState.plan && target ? (
          <>
            <PlanCard plan={planState.plan} target={target} />
            <PlanProductNote plan={planState.plan} />
            <Txt size={12} color={colors.muted}>
              An estimate at today&apos;s rates, after tax. Rates can change and we update your plan when
              they do.
            </Txt>
          </>
        ) : tooSoon ? (
          <Notice tone="warning">
            That is too soon to invest for. The shortest Treasury bill takes 91 days, so choose a later
            date or no date.
          </Notice>
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: "row", alignItems: "center", gap: 16 },
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
