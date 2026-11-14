import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice, StepProgress } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { groupDigits, parseShillings } from "@/goals/amount";
import { useGoals } from "@/goals/GoalsProvider";
import { categoryCopy, describeGoalError, GoalCategory, GoalError, outcomeUnknown } from "@/goals/types";
import { formatDay, formatMoney } from "@/payments/format";
import { DetailList } from "@/payments/Parts";
import { newIdempotencyKey } from "@/payments/idempotency";
import { colors } from "@/theme";
import { IdCheckPrompt } from "@/identity/Parts";

const MINIMUM = 100_000;

/**
 * Step 3 of 3: create the goal, and make the first saving now or later.
 *
 * The first saving is an investment of its own, paid for on the pay screen
 * straight after. One key for the goal and one for the saving are made once per
 * visit, so a second tap or a retry creates one goal and one saving.
 */
export default function StartGoal() {
  const router = useRouter();
  const client = useGoals();
  const params = useLocalSearchParams<{
    category: string;
    name: string;
    target: string;
    targetDate: string;
    saving: string;
  }>();
  // The target comes from the address, which can be edited by hand.
  const target = /^\d{1,13}$/.test(params.target ?? "") ? Number(params.target) : 0;
  const targetDate = params.targetDate ? params.targetDate : null;
  const suggested = Math.max(Number(params.saving) || MINIMUM, MINIMUM);
  const [amount, setAmount] = useState(groupDigits(String(suggested)));
  const [busy, setBusy] = useState<"now" | "later" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Refused because the ID check is not approved yet: the error then opens it.
  const [needsId, setNeedsId] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [goalKey] = useState(newIdempotencyKey);
  const [savingKey] = useState(newIdempotencyKey);
  // After a saving whose answer was lost it may already exist. A retry must be the same saving
  // under the same key, so the amount can no longer change.
  const [locked, setLocked] = useState(false);
  const category = (params.category ?? "other") as GoalCategory;

  const create = async (saveNow: boolean) => {
    setError(null);
    setFieldError(null);
    if (target <= 0) {
      setError(describeGoalError(new GoalError("invalid")));
      return;
    }
    const first = parseShillings(amount);
    if (saveNow && (!first || first < MINIMUM)) {
      setFieldError(`Each saving is at least ${formatMoney({ currency: "UGX", minor: MINIMUM })}.`);
      return;
    }
    setBusy(saveNow ? "now" : "later");
    let savingSent = false;
    try {
      const goal = await client.createGoal(
        {
          name: params.name ?? "",
          category,
          target: { currency: "UGX", minor: target },
          targetDate,
        },
        goalKey,
      );
      if (saveNow && first) {
        savingSent = true;
        const saving = await client.saveToGoal(goal.id, { currency: "UGX", minor: first }, savingKey);
        // Replace, so Back from paying lands on the goal and not on this form.
        router.replace(`/goals/${goal.id}`);
        router.push(`/pay/${saving.investmentId}`);
      } else {
        router.replace(`/goals/${goal.id}`);
      }
    } catch (caught) {
      setNeedsId(caught instanceof GoalError && caught.code === "not_verified");
      if (caught instanceof GoalError && caught.code === "below_minimum") {
        setFieldError(describeGoalError(caught));
      } else {
        setError(describeGoalError(caught));
      }
      if (savingSent && outcomeUnknown(caught)) {
        setLocked(true);
      }
      setBusy(null);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        <>
          <Button
            label={`Create goal and pay ${amount ? `UGX ${amount}` : ""} now`}
            onPress={() => create(true)}
            variant="lime"
            loading={busy === "now"}
            disabled={busy === "later"}
            testID="create-and-save"
          />
          <TextLink label="Create the goal, save later" onPress={() => create(false)} underline={false} />
        </>
      }
    >
      <View style={styles.top}>
        <BackButton fallback="/goals/new" />
        <StepProgress step={3} of={3} />
      </View>
      <View style={{ marginTop: 24, gap: 8 }}>
        <Title>How do you want to start?</Title>
        <Txt color={colors.muted}>
          Make your first saving now and it starts earning tomorrow. You can add to the goal at any time.
        </Txt>
      </View>
      <View style={{ marginTop: 18, gap: 16 }}>
        <DetailList
          rows={[
            { label: "Goal", value: params.name ?? "" },
            { label: "For", value: categoryCopy[category]?.name ?? "Something else" },
            { label: "Target", value: formatMoney({ currency: "UGX", minor: target }) },
            { label: "Needed by", value: targetDate ? formatDay(targetDate) : "No date" },
          ]}
        />
        <TextField
          label="First saving"
          hint="From UGX 100,000. Paid by mobile money or bank transfer on the next screen."
          value={amount}
          onChangeText={(text) => setAmount(groupDigits(text))}
          keyboardType="number-pad"
          inputMode="numeric"
          error={fieldError}
          editable={!locked}
          testID="first-saving"
        />
        <Notice tone="neutral">
          Every saving is its own investment, so nothing sits idle. If a payment does not go through,
          nothing is taken and you can try again.
        </Notice>
        {error ? (needsId ? <IdCheckPrompt message={error} /> : <Notice tone="danger">{error}</Notice>) : null}
        {locked ? (
          <Notice tone="warning">
            Your goal is set up and its first saving may already have reached us, so the amount is
            fixed. Pressing Create goal and pay again sends the same saving. It never makes two.
          </Notice>
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: "row", alignItems: "center", gap: 16 },
});
