import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { TextField } from "@/components/Field";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { DetailErrors, describeDetailProblem, DetailsForm, emptyForm, readForm } from "@/identity/details";
import { useIdentity } from "@/identity/IdentityProvider";
import { StepTop } from "@/identity/Parts";
import { kampalaToday } from "@/identity/photo";
import { hrefFor, stepAfter } from "@/identity/steps";
import { describeIdentityError, documentCopy, IdentityCheck, IdentityError } from "@/identity/types";
import { useIdentityCheck } from "@/identity/useIdentityCheck";
import { colors } from "@/theme";

/** ID check, step 4: the details on the document, as the client reads them from it. */
export default function ConfirmDetails() {
  const state = useIdentityCheck();
  const check = state.status === "ready" ? state.check : null;
  if (state.status === "ready" && !check) return <Redirect href="/id-check/document" />;
  if (check && check.status !== "draft") return <Redirect href="/id-check" />;
  if (!check) {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: "Your details" }} />
        <BackButton fallback="/id-check" />
        <View style={{ marginTop: 28, gap: 16 }}>
          <Title>Your details</Title>
          {state.status === "error" ? (
            <LoadFailed message={state.message} onRetry={state.reload} />
          ) : (
            <Loading label="Loading your ID check" />
          )}
        </View>
      </Screen>
    );
  }
  // Filled from what was confirmed before, if anything.
  return <DetailsFormScreen key={check.id} check={check} />;
}

function DetailsFormScreen({ check }: { check: IdentityCheck }) {
  const router = useRouter();
  const { client } = useIdentity();
  const [form, setForm] = useState<DetailsForm>(() => emptyForm(check.details));
  const [errors, setErrors] = useState<DetailErrors>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (field: keyof DetailsForm) => (value: string) => {
    setForm((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  };

  const save = async () => {
    setError(null);
    const read = readForm(form, kampalaToday());
    if (!read.ok) {
      setErrors(read.errors);
      return;
    }
    setBusy(true);
    try {
      const updated = await client.confirmDetails(read.details);
      const next = stepAfter("details", updated);
      router.push(next ? hrefFor(next) : "/id-check");
    } catch (caught) {
      if (caught instanceof IdentityError && caught.code === "invalid_details" && caught.fields.length > 0) {
        setErrors(Object.fromEntries(caught.fields.map((problem) => [problem.field, describeDetailProblem(problem.code)])));
      }
      setError(describeIdentityError(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={<Button label="These are correct" onPress={save} loading={busy} testID="details-save" />}
    >
      <Stack.Screen options={{ title: "Check your details" }} />
      <StepTop step="details" documentType={check.documentType} fallback="/id-check" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Title>Your details</Title>
        <Txt color={colors.muted}>Type them exactly as they are printed on your ID. We check them against your photos.</Txt>
      </View>

      <View style={{ marginTop: 18, gap: 12 }}>
        <TextField
          label="Surname"
          value={form.surname}
          onChangeText={set("surname")}
          error={errors.surname}
          autoCapitalize="characters"
          autoComplete="name-family"
          testID="details-surname"
        />
        <TextField
          label="Given names"
          value={form.givenNames}
          onChangeText={set("givenNames")}
          error={errors.givenNames}
          autoCapitalize="words"
          autoComplete="name-given"
          testID="details-given-names"
        />
        <TextField
          label={documentCopy[check.documentType].numberLabel}
          value={form.documentNumber}
          onChangeText={set("documentNumber")}
          error={errors.documentNumber}
          autoCapitalize="characters"
          autoCorrect={false}
          testID="details-number"
        />
        <View style={styles.pair}>
          <View style={{ flex: 1 }}>
            <TextField
              label="Date of birth"
              hint="DD/MM/YYYY"
              value={form.dateOfBirth}
              onChangeText={set("dateOfBirth")}
              error={errors.dateOfBirth}
              keyboardType="numbers-and-punctuation"
              placeholder="22/06/1994"
              testID="details-born"
            />
          </View>
          <View style={{ flex: 1 }}>
            <TextField
              label="ID expires"
              hint="DD/MM/YYYY"
              value={form.expiresOn}
              onChangeText={set("expiresOn")}
              error={errors.expiresOn}
              keyboardType="numbers-and-punctuation"
              placeholder="18/09/2030"
              testID="details-expires"
            />
          </View>
        </View>
        <TextField
          label="Tax number (TIN), if you have one"
          value={form.tin}
          onChangeText={set("tin")}
          error={errors.tin}
          keyboardType="number-pad"
          placeholder="You can leave this empty"
          testID="details-tin"
        />
        {error ? <Notice tone="danger">{error}</Notice> : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  pair: { flexDirection: "row", gap: 12 },
});
