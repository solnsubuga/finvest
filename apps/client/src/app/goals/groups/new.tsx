import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { groupDigits, kampalaToday, monthYear, parseShillings } from "@/goals/amount";
import { addMonths } from "@/goals/dates";
import { useGroups } from "@/goals/GoalsProvider";
import { describeGroupError, groupOutcomeUnknown, MAX_DISPLAY_NAME } from "@/goals/groups";
import { CATEGORIES, categoryCopy, GoalCategory } from "@/goals/types";
import { newIdempotencyKey } from "@/payments/idempotency";
import { colors } from "@/theme";

const HORIZONS = [6, 12, 18, 24, 36, 60] as const;
type Horizon = (typeof HORIZONS)[number] | "none";

function horizonLabel(option: Horizon): string {
  if (option === "none") return "No date";
  if (option % 12 !== 0) return `${option} months`;
  return option === 12 ? "1 year" : `${option / 12} years`;
}

type Problems = Partial<Record<"category" | "name" | "target" | "part" | "you", string>>;

/** Set up a group goal: the shared target, and the client's own part of it. */
export default function NewGroup() {
  const router = useRouter();
  const client = useGroups();
  const [category, setCategory] = useState<GoalCategory | null>(null);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [horizon, setHorizon] = useState<Horizon>(24);
  const [part, setPart] = useState("");
  const [you, setYou] = useState("");
  const [problems, setProblems] = useState<Problems>({});
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  // Kept for retries, so a lost answer never sets up a second group.
  const [key] = useState(newIdempotencyKey);
  const [locked, setLocked] = useState(false);

  const targetDate = horizon === "none" ? null : addMonths(kampalaToday(), horizon);

  const choose = (chosen: GoalCategory) => {
    setCategory(chosen);
    if (!name || CATEGORIES.some((c) => categoryCopy[c].name === name)) {
      setName(chosen === "other" ? "" : categoryCopy[chosen].name);
    }
  };

  const submit = async () => {
    const cleanName = name.trim().replace(/\s+/g, " ");
    const cleanYou = you.trim().replace(/\s+/g, " ");
    const total = parseShillings(target);
    const own = parseShillings(part);
    const found: Problems = {};
    if (!category) found.category = "Choose what the group is saving for.";
    if (!cleanName) found.name = "Give the group goal a name.";
    else if (cleanName.length > 60) found.name = "Keep the name to 60 characters.";
    if (!total) found.target = "Enter how much the group needs, in shillings.";
    if (!own) found.part = "Enter your own part, in shillings.";
    else if (total && own > total) found.part = "Your part cannot be more than the group's target.";
    if (!cleanYou) found.you = "Enter the name the others will see.";
    else if (cleanYou.length > MAX_DISPLAY_NAME) found.you = `Keep it to ${MAX_DISPLAY_NAME} characters.`;
    setProblems(found);
    if (Object.keys(found).length > 0 || !category || !total || !own) return;

    setSending(true);
    setFailure(null);
    try {
      const group = await client.createGroup(
        {
          name: cleanName,
          category,
          target: { currency: "UGX", minor: total },
          targetDate,
          displayName: cleanYou,
          ownTarget: { currency: "UGX", minor: own },
        },
        key,
      );
      router.replace(`/goals/groups/${group.id}`);
    } catch (caught) {
      if (groupOutcomeUnknown(caught)) setLocked(true);
      setFailure(describeGroupError(caught));
    } finally {
      setSending(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={<Button label="Set up the group goal" onPress={submit} loading={sending} testID="group-create" />}
    >
      <BackButton fallback="/goals" />
      <View style={{ marginTop: 20, gap: 8 }}>
        <Title>Save together</Title>
        <Txt color={colors.muted}>
          Set one target with family or friends. Each of you saves into your own goal, invested in your
          own name. Nobody else can touch your money, and you can leave at any time with your savings.
        </Txt>
      </View>

      <View style={styles.grid} accessibilityRole="radiogroup">
        {CATEGORIES.map((option) => {
          const copy = categoryCopy[option];
          const on = option === category;
          return (
            <Pressable
              key={option}
              accessibilityRole="radio"
              accessibilityState={{ checked: on }}
              accessibilityLabel={copy.name}
              onPress={() => choose(option)}
              style={[styles.chip, on && styles.chipOn]}
              testID={`group-category-${option}`}
            >
              <Txt size={13} weight="bold" color={on ? colors.white : colors.ink}>
                {copy.name}
              </Txt>
            </Pressable>
          );
        })}
      </View>
      {problems.category ? (
        <Txt size={13} color={colors.danger} style={{ marginTop: 6 }}>
          {problems.category}
        </Txt>
      ) : null}

      <View style={{ marginTop: 18, gap: 16 }}>
        <TextField
          label="Name the group goal"
          value={name}
          onChangeText={setName}
          placeholder="Family land in Mukono"
          maxLength={80}
          error={problems.name}
          editable={!locked}
          testID="group-name"
        />
        <TextField
          label="How much does the group need?"
          hint="All of you together, in shillings"
          value={target}
          onChangeText={(text) => setTarget(groupDigits(text))}
          keyboardType="number-pad"
          inputMode="numeric"
          placeholder="12,000,000"
          error={problems.target}
          editable={!locked}
          testID="group-target"
        />
        <View style={{ gap: 8 }}>
          <Txt size={13} weight="bold">
            By when?
          </Txt>
          <View style={styles.chips} accessibilityRole="radiogroup">
            {[...HORIZONS, "none" as const].map((option) => {
              const on = option === horizon;
              return (
                <Pressable
                  key={String(option)}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: on }}
                  onPress={() => !locked && setHorizon(option)}
                  style={[styles.chip, on && styles.chipOn]}
                  testID={`group-horizon-${option}`}
                >
                  <Txt size={13} weight="bold" color={on ? colors.white : colors.ink}>
                    {horizonLabel(option)}
                  </Txt>
                </Pressable>
              );
            })}
          </View>
          <Txt size={13} color={colors.muted}>
            {targetDate ? `Needed in ${monthYear(targetDate)}.` : "No deadline."}
          </Txt>
        </View>
        <TextField
          label="Your part"
          hint="The target of your own goal in the group. The others choose theirs when they join."
          value={part}
          onChangeText={(text) => setPart(groupDigits(text))}
          keyboardType="number-pad"
          inputMode="numeric"
          placeholder="4,000,000"
          error={problems.part}
          editable={!locked}
          testID="group-part"
        />
        <TextField
          label="Your name in the group"
          hint="The others see this name and the group's combined total, never what you save."
          value={you}
          onChangeText={setYou}
          placeholder="Grace"
          maxLength={40}
          error={problems.you}
          editable={!locked}
          testID="group-you"
        />
        {failure ? <Notice tone="danger">{failure}</Notice> : null}
        {locked ? (
          <Notice tone="warning">
            We did not hear back. Pressing again sends the same group goal, so it is never set up twice.
          </Notice>
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  grid: { marginTop: 18, flexDirection: "row", flexWrap: "wrap", gap: 8 },
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
