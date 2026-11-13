import { useEffect, useState } from "react";
import { View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { groupDigits, parseShillings } from "@/goals/amount";
import { useGoals } from "@/goals/GoalsProvider";
import { describeGoalError, Goal, GoalError } from "@/goals/types";
import { formatMoney } from "@/payments/format";
import { newIdempotencyKey } from "@/payments/idempotency";
import { Loading } from "@/payments/Parts";
import { colors } from "@/theme";

const MINIMUM = 100_000;

/** Save to a goal now: an amount, then the pay screen. One key per visit. */
export default function SaveToGoal() {
  const router = useRouter();
  const client = useGoals();
  const { goalId } = useLocalSearchParams<{ goalId: string }>();
  const [goal, setGoal] = useState<Goal | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [idempotencyKey] = useState(newIdempotencyKey);

  useEffect(() => {
    let live = true;
    client.getGoal(goalId).then(
      (found) => {
        if (!live) {
          return;
        }
        setGoal(found);
        setAmount(groupDigits(String(found.plan?.saving.minor ?? MINIMUM)));
      },
      (caught) => live && setLoadError(describeGoalError(caught)),
    );
    return () => {
      live = false;
    };
  }, [client, goalId]);

  const save = async () => {
    setError(null);
    setFieldError(null);
    const value = parseShillings(amount);
    if (!value || value < MINIMUM) {
      setFieldError(`Each saving is at least ${formatMoney({ currency: "UGX", minor: MINIMUM })}.`);
      return;
    }
    setBusy(true);
    try {
      const saving = await client.saveToGoal(goalId, { currency: "UGX", minor: value }, idempotencyKey);
      router.replace(`/pay/${saving.investmentId}`);
    } catch (caught) {
      if (caught instanceof GoalError && caught.code === "below_minimum") {
        setFieldError(describeGoalError(caught));
      } else {
        setError(describeGoalError(caught));
      }
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        goal ? (
          <Button
            label={amount ? `Continue to pay UGX ${amount}` : "Continue to pay"}
            onPress={save}
            loading={busy}
            testID="save-continue"
          />
        ) : null
      }
    >
      <BackButton fallback={`/goals/${goalId}`} />
      {loadError ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">{loadError}</Notice>
        </View>
      ) : !goal ? (
        <Loading label="Loading your goal" />
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <View style={{ gap: 8 }}>
            <Title>{`Save to ${goal.name}`}</Title>
            <Txt color={colors.muted}>
              {goal.plan
                ? `Your plan is ${formatMoney(goal.plan.saving)} a month. Any amount from UGX 100,000 helps.`
                : "Any amount from UGX 100,000."}
            </Txt>
          </View>
          <TextField
            label="Amount"
            hint="In shillings. You pay by mobile money or bank transfer next."
            value={amount}
            onChangeText={(text) => setAmount(groupDigits(text))}
            keyboardType="number-pad"
            inputMode="numeric"
            error={fieldError}
            testID="save-amount"
          />
          <Notice tone="neutral">
            This saving is invested the day it is paid, in the same Treasury bill as the rest of the goal,
            and rolls over when it matures.
          </Notice>
          {error ? <Notice tone="danger">{error}</Notice> : null}
        </View>
      )}
    </Screen>
  );
}
