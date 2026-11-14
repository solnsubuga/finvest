import { useCallback, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";
import { Stack, useFocusEffect } from "expo-router";

import { formatE164, toE164 } from "@/auth/phone";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { PhoneField, TextField } from "@/components/Field";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { useProfile } from "@/profile/ProfileProvider";
import { bpsToPercent, evenShares, percentToBps } from "@/profile/shares";
import { describeProfileError, Person, ProfileError, Relationship, RELATIONSHIPS } from "@/profile/types";
import { colors, fontSizes, radii } from "@/theme";

const MAX_PEOPLE = 5;

type State =
  | { status: "loading" }
  | { status: "ready"; people: Person[] }
  | { status: "error"; message: string };

type Draft = {
  fullName: string;
  relationship: Relationship | null;
  /** National digits as typed, without +256. */
  phone: string;
  idNumber: string;
  share: string;
};

const WORDS = {
  full_name_needed: "Write their full name, as on their ID.",
  required: "Write their full name, as on their ID.",
  invalid_phone: "That is not a mobile number we can call.",
  your_own_number: "This is your own number. Give theirs.",
  invalid_id_number: "Write their ID number, letters and digits only.",
  named_twice: "This person is already on your list.",
  invalid_share: "Give a share between 0.01% and 100%.",
} as const;
const FIELD_WORDS: Readonly<Record<string, string | undefined>> = WORDS;

const relationshipLabel = (value: Relationship) =>
  RELATIONSHIPS.find((item) => item.value === value)?.label ?? "Someone else";

/** Profile, Next of kin: who we contact if we cannot reach the client, and their shares. */
export default function NextOfKinScreen() {
  const profile = useProfile();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState<{ index: number | null; draft: Draft } | null>(null);
  const [draftErrors, setDraftErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<{ tone: "neutral" | "danger"; text: string } | null>(null);

  useFocusEffect(
    useCallback(() => {
      let live = true;
      profile.getNextOfKin().then(
        (found) => live && setState({ status: "ready", people: found.people }),
        (caught: unknown) => live && setState({ status: "error", message: describeProfileError(caught) }),
      );
      return () => {
        live = false;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [profile, attempt]),
  );

  const people = state.status === "ready" ? state.people : [];

  const save = async (next: Person[], done: string) => {
    setSaving(true);
    setNote(null);
    try {
      const saved = await profile.saveNextOfKin(next);
      setState({ status: "ready", people: saved.people });
      setEditing(null);
      setNote({ tone: "neutral", text: done });
    } catch (caught) {
      if (caught instanceof ProfileError && caught.code === "invalid") {
        const index = editing?.index ?? next.length - 1;
        const mine: Record<string, string> = {};
        let other: string | null = null;
        for (const problem of caught.fields) {
          const match = /^people\.(\d+)\.(\w+)$/.exec(problem.field);
          if (match?.[2] && Number(match[1]) === index) {
            mine[match[2]] = FIELD_WORDS[problem.code] ?? "Check this.";
          } else if (problem.code === "shares_must_total_100") {
            other = "Shares must add up to 100%. Change the shares so they do.";
          } else {
            other = "Some details on your list need fixing.";
          }
        }
        setDraftErrors(mine);
        if (other) setNote({ tone: "danger", text: other });
      } else {
        setNote({ tone: "danger", text: describeProfileError(caught) });
      }
    } finally {
      setSaving(false);
    }
  };

  const startAdding = () => {
    setDraftErrors({});
    setNote(null);
    // A new person takes an even share, and everyone's shares are shared out again.
    const share = bpsToPercent(evenShares(people.length + 1).at(-1) ?? 10_000).replace("%", "");
    setEditing({ index: null, draft: { fullName: "", relationship: null, phone: "", idNumber: "", share } });
  };

  const startEditing = (index: number) => {
    const person = people[index];
    if (!person) return;
    setDraftErrors({});
    setNote(null);
    setEditing({
      index,
      draft: {
        fullName: person.fullName,
        relationship: person.relationship,
        phone: person.phone.startsWith("+256") ? person.phone.slice(4) : person.phone,
        idNumber: person.idNumber,
        share: bpsToPercent(person.shareBps).replace("%", ""),
      },
    });
  };

  const submitDraft = () => {
    if (!editing) return;
    const { draft, index } = editing;
    const problems: Record<string, string> = {};
    if (draft.fullName.trim().split(/\s+/).length < 2) problems.full_name = WORDS.full_name_needed;
    if (!draft.relationship) problems.relationship = "Choose how you know them.";
    const phone = toE164(draft.phone);
    if (!phone) problems.phone = WORDS.invalid_phone;
    if (!/^[A-Za-z0-9 -]{5,40}$/.test(draft.idNumber.trim())) problems.id_number = WORDS.invalid_id_number;
    const share = percentToBps(draft.share);
    if (share === null) problems.share_bps = WORDS.invalid_share;
    setDraftErrors(problems);
    if (Object.keys(problems).length || !phone || share === null || !draft.relationship) return;
    const person: Person = {
      fullName: draft.fullName.trim(),
      relationship: draft.relationship,
      phone,
      idNumber: draft.idNumber.trim(),
      shareBps: share,
    };
    let next: Person[];
    if (index === null) {
      // Adding someone shares everyone else's part out evenly with the new share.
      const rest = evenShares(people.length).map((bps) => Math.floor((bps * (10_000 - share)) / 10_000));
      const spare = 10_000 - share - rest.reduce((sum, bps) => sum + bps, 0);
      next = [...people.map((p, i) => ({ ...p, shareBps: (rest[i] ?? 0) + (i === 0 ? spare : 0) })), person];
      if (next.some((p) => p.shareBps < 1)) {
        setDraftErrors({ share_bps: "Leave some share for the others, or remove someone first." });
        return;
      }
    } else {
      next = people.map((p, i) => (i === index ? person : p));
    }
    void save(next, index === null ? `${person.fullName} is on your list.` : "Saved.");
  };

  const remove = (index: number) => {
    const left = people.filter((_, i) => i !== index);
    const shares = evenShares(left.length);
    void save(
      left.map((p, i) => ({ ...p, shareBps: shares[i] ?? 0 })),
      left.length ? "Removed. The others now share it evenly." : "Removed. Your list is empty.",
    );
  };

  const total = people.reduce((sum, p) => sum + p.shareBps, 0);

  return (
    <Screen signedIn>
      <Stack.Screen options={{ title: "Next of kin" }} />
      <BackButton fallback="/profile" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Profile</Eyebrow>
        <Title>Next of kin</Title>
        <Txt color={colors.muted}>
          If we cannot reach you, or if something happens to you, these are the people we will speak to.
        </Txt>
      </View>

      {state.status === "loading" ? (
        <View style={{ marginTop: 40 }}>
          <ActivityIndicator accessibilityLabel="Loading" color={colors.black} />
        </View>
      ) : null}

      {state.status === "error" ? (
        <View style={{ marginTop: 22, gap: 12 }}>
          <Notice tone="danger">{state.message}</Notice>
          <Button label="Try again" onPress={() => setAttempt((n) => n + 1)} height={52} />
        </View>
      ) : null}

      {state.status === "ready" ? (
        <View style={{ marginTop: 22, gap: 14 }}>
          {note ? <Notice tone={note.tone}>{note.text}</Notice> : null}
          {people.length ? (
            <Card padded={false}>
              {people.map((person, index) => (
                <View key={`${person.idNumber}-${index}`} style={[styles.row, index > 0 && styles.divider]} testID={`kin-${index}`}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`Change ${person.fullName}`}
                    onPress={() => startEditing(index)}
                    style={({ pressed }) => [styles.person, pressed && { opacity: 0.7 }]}
                  >
                    <View style={{ flex: 1 }}>
                      <Txt weight="bold">{person.fullName}</Txt>
                      <Txt size={fontSizes.sm} color={colors.muted}>
                        {relationshipLabel(person.relationship)}, {formatE164(person.phone)}
                      </Txt>
                    </View>
                    <Txt size={fontSizes.sm} weight="bold">
                      {bpsToPercent(person.shareBps)}
                    </Txt>
                    <Icon name="chevron" size={16} strokeWidth={2.2} />
                  </Pressable>
                </View>
              ))}
            </Card>
          ) : (
            <Card>
              <View style={{ gap: 6 }} testID="kin-empty">
                <Txt weight="bold">Nobody named yet</Txt>
                <Txt size={fontSizes.sm} color={colors.muted}>
                  Add up to {MAX_PEOPLE} people. We only contact them if we cannot reach you.
                </Txt>
              </View>
            </Card>
          )}

          <View style={[styles.info, { gap: 6 }]}>
            <Txt size={fontSizes.sm}>
              Shares must add up to 100%{people.length ? ` (now ${bpsToPercent(total)})` : ""}. Each person needs a
              mobile number and their ID number, so we can confirm who they are.
            </Txt>
            <Txt size={fontSizes.sm}>
              The shares record your wishes. They are not a will: anything paid to your next of kin follows the
              documents the law asks for.
            </Txt>
          </View>

          {editing ? (
            <Card>
              <View style={{ gap: 14 }} testID="kin-form">
                <Txt weight="bold">{editing.index === null ? "Add a person" : `Change ${people[editing.index]?.fullName}`}</Txt>
                <TextField
                  label="Full name as on their ID"
                  value={editing.draft.fullName}
                  onChangeText={(fullName) => setEditing({ ...editing, draft: { ...editing.draft, fullName } })}
                  autoComplete="off"
                  error={draftErrors.full_name}
                  testID="kin-name"
                />
                <View style={{ gap: 8 }}>
                  <Txt size={fontSizes.sm} weight="bold">
                    Relationship
                  </Txt>
                  <View style={styles.chips} accessibilityRole="radiogroup" accessibilityLabel="Relationship">
                    {RELATIONSHIPS.map((item) => {
                      const on = editing.draft.relationship === item.value;
                      return (
                        <Pressable
                          key={item.value}
                          accessibilityRole="radio"
                          aria-checked={on}
                          onPress={() => setEditing({ ...editing, draft: { ...editing.draft, relationship: item.value } })}
                          style={({ pressed }) => [styles.chip, on && styles.chipOn, pressed && { opacity: 0.7 }]}
                          testID={`kin-relationship-${item.value}`}
                        >
                          <Txt size={fontSizes.sm} weight="semibold" color={on ? colors.white : colors.ink}>
                            {item.label}
                          </Txt>
                        </Pressable>
                      );
                    })}
                  </View>
                  {draftErrors.relationship ? (
                    <Txt size={fontSizes.xs} weight="semibold" color={colors.danger}>
                      {draftErrors.relationship}
                    </Txt>
                  ) : null}
                </View>
                <PhoneField
                  value={editing.draft.phone}
                  onChangeText={(phone) => setEditing({ ...editing, draft: { ...editing.draft, phone } })}
                  error={draftErrors.phone}
                />
                <TextField
                  label="Their ID number"
                  value={editing.draft.idNumber}
                  onChangeText={(idNumber) => setEditing({ ...editing, draft: { ...editing.draft, idNumber } })}
                  autoCapitalize="characters"
                  autoComplete="off"
                  hint="The NIN on their national ID, or their passport number"
                  error={draftErrors.id_number}
                  testID="kin-id"
                />
                <TextField
                  label="Their share, in %"
                  value={editing.draft.share}
                  onChangeText={(share) => setEditing({ ...editing, draft: { ...editing.draft, share } })}
                  keyboardType="decimal-pad"
                  inputMode="decimal"
                  hint={
                    editing.index === null && people.length
                      ? "Everyone else shares what is left evenly. You can change each share after."
                      : undefined
                  }
                  error={draftErrors.share_bps}
                  testID="kin-share"
                />
                <Button
                  label={editing.index === null ? "Add this person" : "Save changes"}
                  onPress={submitDraft}
                  loading={saving}
                  height={52}
                  testID="kin-save"
                />
                {editing.index !== null ? (
                  <TextLink label="Remove this person" onPress={() => remove(editing.index as number)} testID="kin-remove" />
                ) : null}
                <TextLink label="Cancel" onPress={() => setEditing(null)} />
              </View>
            </Card>
          ) : people.length < MAX_PEOPLE ? (
            <Button
              label={people.length ? "Add another person" : "Add a person"}
              variant={people.length ? "secondary" : "primary"}
              onPress={startAdding}
              height={52}
              testID="kin-add"
            />
          ) : null}
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { paddingHorizontal: 16 },
  person: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 14, minHeight: 64 },
  divider: { borderTopWidth: 1, borderTopColor: colors.border },
  info: { backgroundColor: colors.surfaceSunken, borderRadius: radii.md, paddingVertical: 12, paddingHorizontal: 14 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    minHeight: 40,
    paddingHorizontal: 14,
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.white,
    justifyContent: "center",
  },
  chipOn: { backgroundColor: colors.black, borderColor: colors.black },
});
