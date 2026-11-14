import { useState } from "react";
import { Pressable, ScrollView, StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { countryName, NEARBY, OTHERS } from "@/identity/countries";
import { useIdentity } from "@/identity/IdentityProvider";
import { StepTop } from "@/identity/Parts";
import { hrefFor, stepAfter } from "@/identity/steps";
import { describeIdentityError, DOCUMENT_TYPES, documentCopy, DocumentType, IdentityCheck } from "@/identity/types";
import { useIdentityCheck } from "@/identity/useIdentityCheck";
import { colors, fontSizes } from "@/theme";

function Chip({ label, selected, onPress, testID }: { label: string; selected: boolean; onPress: () => void; testID?: string }) {
  return (
    <Pressable
      accessibilityRole="radio"
      aria-checked={selected}
      onPress={onPress}
      testID={testID}
      style={({ pressed }) => [styles.chip, selected && styles.chipOn, pressed && { opacity: 0.7 }]}
    >
      <Txt size={fontSizes.sm} weight="semibold" color={selected ? colors.white : colors.ink}>
        {label}
      </Txt>
    </Pressable>
  );
}

/** A small drawing of a card: a photo square and a line of text. */
function CardMark() {
  return (
    <View style={styles.cardMark}>
      <View style={styles.cardPhoto} />
      <View style={styles.cardLine} />
    </View>
  );
}

/** ID check, step 1: which document, and the country that issued it. */
export default function ChooseDocument() {
  const state = useIdentityCheck();
  const check = state.status === "ready" ? state.check : null;
  if (check && check.status !== "draft") {
    // Handed in: the document cannot be changed any more.
    return <Redirect href="/id-check" />;
  }
  if (state.status !== "ready") {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: "Which ID will you use?" }} />
        <StepTop step="document" documentType={null} fallback="/id-check" />
        <View style={{ marginTop: 28, gap: 16 }}>
          <Title>Which ID will you use?</Title>
          {state.status === "loading" ? (
            <Loading label="Loading your ID check" />
          ) : (
            <LoadFailed message={state.message} onRetry={state.reload} />
          )}
        </View>
      </Screen>
    );
  }
  // Come back to this step and the choice made before is still chosen.
  return <DocumentForm key={check?.id ?? "new"} check={check} />;
}

function DocumentForm({ check }: { check: IdentityCheck | null }) {
  const router = useRouter();
  const { client } = useIdentity();
  const [documentType, setDocumentType] = useState<DocumentType>(check?.documentType ?? "national_id");
  const [country, setCountry] = useState(check?.documentCountry ?? "UG");
  const [showOthers, setShowOthers] = useState(
    check ? !NEARBY.some((nearby) => nearby.code === check.documentCountry) : false,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await client.chooseDocument(documentType, country);
      const next = stepAfter("document", updated);
      router.push(next ? hrefFor(next) : "/id-check");
    } catch (caught) {
      setError(describeIdentityError(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        <Button label={`Continue with ${documentCopy[documentType].name}`} onPress={go} loading={busy} testID="document-continue" />
      }
    >
      <Stack.Screen options={{ title: "Which ID will you use?" }} />
      <StepTop step="document" documentType={documentType} fallback="/id-check" />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>Which ID will you use?</Title>
        <Txt color={colors.muted}>The law asks us to confirm who you are before you invest. It takes about two minutes.</Txt>
      </View>

      <View style={{ marginTop: 22, gap: 18 }}>
        <View style={{ gap: 8 }} accessibilityRole="radiogroup" accessibilityLabel="Country that issued it">
          <Txt size={fontSizes.sm} weight="bold">
            Country that issued it
          </Txt>
          <View style={styles.chips}>
            {NEARBY.map((option) => (
              <Chip
                key={option.code}
                label={option.name}
                selected={country === option.code}
                onPress={() => {
                  setCountry(option.code);
                  setShowOthers(false);
                }}
                testID={`country-${option.code}`}
              />
            ))}
            <Chip
              label={showOthers && !NEARBY.some((n) => n.code === country) ? countryName(country) : "Another country"}
              selected={showOthers}
              onPress={() => setShowOthers(true)}
              testID="country-other"
            />
          </View>
          {showOthers ? (
            <ScrollView style={styles.others} nestedScrollEnabled>
              {OTHERS.map((option) => (
                <Pressable
                  key={option.code}
                  accessibilityRole="radio"
                  aria-checked={country === option.code}
                  onPress={() => setCountry(option.code)}
                  style={({ pressed }) => [styles.other, pressed && { opacity: 0.7 }]}
                >
                  <Txt size={fontSizes.sm} weight={country === option.code ? "bold" : "regular"}>
                    {option.name}
                  </Txt>
                  {country === option.code ? <View style={styles.dot} /> : null}
                </Pressable>
              ))}
            </ScrollView>
          ) : null}
        </View>

        <View style={{ gap: 10 }} accessibilityRole="radiogroup" accessibilityLabel="Document">
          {DOCUMENT_TYPES.map((type) => {
            const selected = documentType === type;
            return (
              <Pressable
                key={type}
                accessibilityRole="radio"
                aria-checked={selected}
                onPress={() => setDocumentType(type)}
                testID={`document-${type}`}
                style={({ pressed }) => [styles.option, selected && styles.optionOn, pressed && { opacity: 0.8 }]}
              >
                <CardMark />
                <View style={{ flex: 1 }}>
                  <Txt weight="bold">{documentCopy[type].name}</Txt>
                  <Txt size={fontSizes.sm} color={colors.muted}>
                    {documentCopy[type].sub}
                  </Txt>
                </View>
                <View style={[styles.radio, selected && styles.radioOn]}>
                  {selected ? <View style={styles.radioDot} /> : null}
                </View>
              </Pressable>
            );
          })}
        </View>
        <Txt size={fontSizes.sm} color={colors.muted}>
          Have the original with you. Photocopies and photos of a screen are not accepted.
        </Txt>
        {check?.detailsConfirmed && (check.documentType !== documentType || check.documentCountry !== country) ? (
          <Notice tone="warning">Changing the document means checking your details again against the new one.</Notice>
        ) : null}
        {error ? <Notice tone="danger">{error}</Notice> : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    minHeight: 40,
    paddingHorizontal: 14,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  chipOn: { backgroundColor: colors.black, borderColor: colors.black },
  others: {
    maxHeight: 240,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 16,
    backgroundColor: colors.surface,
  },
  other: {
    minHeight: 44,
    paddingHorizontal: 14,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.black },
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  optionOn: { borderWidth: 2, borderColor: colors.black, paddingVertical: 13, paddingHorizontal: 15 },
  cardMark: {
    width: 44,
    height: 32,
    borderRadius: 6,
    backgroundColor: colors.surfaceSunken,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 5,
    gap: 4,
  },
  cardPhoto: { width: 10, height: 12, borderRadius: 2, backgroundColor: colors.black },
  cardLine: { flex: 1, height: 3, borderRadius: 2, backgroundColor: colors.neutral },
  radio: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 2,
    borderColor: colors.neutral,
    alignItems: "center",
    justifyContent: "center",
  },
  radioOn: { backgroundColor: colors.black, borderColor: colors.black },
  radioDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.white },
});
