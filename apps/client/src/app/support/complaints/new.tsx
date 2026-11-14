import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Field, TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { StackHeader } from "@/components/StackHeader";
import { Txt } from "@/components/Txt";
import { newIdempotencyKey } from "@/payments/idempotency";
import { complaintCategories } from "@/support/labels";
import { useSupport } from "@/support/SupportProvider";
import {
  ComplaintCategory,
  describeSupportError,
  MAX_DETAILS,
  MAX_SUMMARY,
  SupportError,
} from "@/support/types";
import { colors } from "@/theme";

type Problems = { category?: string; summary?: string; details?: string };

/** Make a complaint: what it is about, one line, and the full story. */
export default function NewComplaint() {
  const router = useRouter();
  const { client, refreshUnread } = useSupport();
  const [category, setCategory] = useState<ComplaintCategory | null>(null);
  const [summary, setSummary] = useState("");
  const [details, setDetails] = useState("");
  const [problems, setProblems] = useState<Problems>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // One key for this form, reused on every try after a lost answer, so the complaint is logged
  // once. A definite refusal logged nothing, so the next try gets a fresh key.
  const [key, setKey] = useState(newIdempotencyKey);
  // After a try whose answer was lost the complaint may already be logged, and the API answers
  // the same key with that first complaint. So the words can no longer change.
  const [locked, setLocked] = useState(false);

  /** An error goes as soon as the client starts to put it right. */
  const clear = (field: keyof Problems) =>
    setProblems((current) => (current[field] ? { ...current, [field]: undefined } : current));

  const submit = async () => {
    const found: Problems = {};
    if (!category) {
      found.category = "Choose what it is about.";
    }
    if (summary.trim().length === 0) {
      found.summary = "Say in one line what went wrong.";
    }
    if (details.trim().length === 0) {
      found.details = "Tell us what happened.";
    }
    setProblems(found);
    setFailure(null);
    if (!category || found.summary || found.details) {
      return;
    }
    setBusy(true);
    try {
      const complaint = await client.makeComplaint({
        category,
        summary: summary.trim(),
        details: details.trim(),
        key,
      });
      void refreshUnread();
      // Replace, so Back from the complaint does not return to a filled form.
      router.replace({
        pathname: "/support/complaints/[complaintId]",
        params: { complaintId: complaint.id, made: "1" },
      });
    } catch (error) {
      if (error instanceof SupportError && error.code !== "unavailable") {
        // Refused, so nothing was logged under this key, even if an earlier try was lost.
        setKey(newIdempotencyKey());
        setLocked(false);
      } else {
        setLocked(true);
      }
      setFailure(describeSupportError(error));
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        <>
          {failure ? <Notice tone="danger">{failure}</Notice> : null}
          {locked ? (
            <Notice tone="warning">
              Your complaint may already have reached us, so it can no longer be changed. Pressing Send
              complaint again sends the same complaint. It never logs two.
            </Notice>
          ) : null}
          <Button label="Send complaint" onPress={submit} loading={busy} testID="send-complaint" />
        </>
      }
    >
      <Stack.Screen options={{ title: "Make a complaint" }} />
      <StackHeader title="Make a complaint" fallback="/support/complaints" />
      <View style={{ marginTop: 20, gap: 18 }}>
        <Field label="What is it about?" error={problems.category}>
          <View style={styles.choices} accessibilityRole="radiogroup">
            {complaintCategories.map((choice) => {
              const chosen = category === choice.category;
              return (
                <Pressable
                  key={choice.category}
                  accessibilityRole="radio"
                  aria-checked={chosen}
                  aria-disabled={locked}
                  disabled={locked}
                  onPress={() => {
                    setCategory(choice.category);
                    clear("category");
                  }}
                  style={({ pressed }) => [
                    styles.choice,
                    chosen && styles.chosen,
                    pressed && { opacity: 0.7 },
                  ]}
                  testID={`category-${choice.category}`}
                >
                  <Txt size={13} weight="semibold" color={chosen ? colors.white : colors.ink}>
                    {choice.title}
                  </Txt>
                </Pressable>
              );
            })}
          </View>
        </Field>
        <TextField
          label="In one line, what went wrong?"
          value={summary}
          onChangeText={(text) => {
            setSummary(text);
            clear("summary");
          }}
          maxLength={MAX_SUMMARY}
          editable={!locked}
          error={problems.summary}
          testID="complaint-summary"
        />
        <TextField
          label="What happened?"
          hint="Dates, amounts and what you were told help us answer faster. Do not include your password or PIN."
          value={details}
          onChangeText={(text) => {
            setDetails(text);
            clear("details");
          }}
          maxLength={MAX_DETAILS}
          editable={!locked}
          multiline
          textAlignVertical="top"
          style={{ height: 150, paddingTop: 14, paddingBottom: 14 }}
          error={problems.details}
          testID="complaint-details"
        />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  choices: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  choice: {
    height: 44,
    paddingHorizontal: 14,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    justifyContent: "center",
  },
  chosen: { backgroundColor: colors.black, borderColor: colors.black },
});
