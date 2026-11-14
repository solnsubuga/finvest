import { useCallback, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";
import { Stack, useFocusEffect, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatAccountNumber } from "@/identity/types";
import { useOrganisations } from "@/organisations/OrganisationsProvider";
import { describeOrganisationError, KIND_LABEL, Organisation } from "@/organisations/types";
import { Pill, standingOf } from "@/organisations/ui";
import { colors, fontSizes, radii } from "@/theme";

type State =
  | { status: "loading" }
  | { status: "ready"; items: Organisation[] }
  | { status: "error"; message: string };

/** Profile, Company and group accounts: every application the client made or is asked to sign. */
export default function OrganisationsScreen() {
  const organisations = useOrganisations();
  const router = useRouter();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useFocusEffect(
    useCallback(() => {
      let live = true;
      organisations.list().then(
        (items) => live && setState({ status: "ready", items }),
        (caught: unknown) => live && setState({ status: "error", message: describeOrganisationError(caught) }),
      );
      return () => {
        live = false;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [organisations, attempt]),
  );

  const items = state.status === "ready" ? state.items : [];
  const asked = items.filter((o) => o.invited);
  const mine = items.filter((o) => !o.invited);

  const row = (found: Organisation, index: number) => {
    const standing = standingOf(found);
    const detail = found.accountNumber
      ? `${KIND_LABEL[found.kind]}, ${formatAccountNumber(found.accountNumber)}`
      : found.invited
        ? `${found.signatories.find((s) => s.isApplicant)?.name ?? "Someone"} asked you`
        : KIND_LABEL[found.kind];
    return (
      <Pressable
        key={found.id}
        accessibilityRole="button"
        accessibilityLabel={`${found.name}, ${standing.label}`}
        onPress={() => router.push({ pathname: "/account/organisations/[organisationId]", params: { organisationId: found.id } })}
        style={({ pressed }) => [styles.row, index > 0 && styles.divider, pressed && { opacity: 0.7 }]}
        testID={`organisation-${found.id}`}
      >
        <View style={{ flex: 1, gap: 2 }}>
          <Txt weight="bold">{found.name}</Txt>
          <Txt size={fontSizes.sm} color={colors.muted}>
            {detail}
          </Txt>
        </View>
        <Pill {...standing} />
        <Icon name="chevron" size={16} strokeWidth={2.2} />
      </Pressable>
    );
  };

  return (
    <Screen
      signedIn
      footer={
        state.status === "ready" ? (
          <Button
            label={mine.length ? "Open another account" : "Open a company or group account"}
            onPress={() => router.push("/account/organisations/new")}
            testID="organisation-new"
          />
        ) : undefined
      }
    >
      <Stack.Screen options={{ title: "Company and group accounts" }} />
      <BackButton fallback="/profile" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Profile</Eyebrow>
        <Title>Company and group accounts</Title>
        <Txt color={colors.muted}>
          A business, savings group or SACCO gets its own Finvest account number, with the people who sign for it.
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
          {asked.length ? (
            <View style={{ gap: 8 }}>
              <Txt size={fontSizes.xs} weight="bold" color={colors.muted} style={styles.label}>
                Asked to sign
              </Txt>
              <Card padded={false}>{asked.map(row)}</Card>
            </View>
          ) : null}
          {mine.length ? (
            <View style={{ gap: 8 }}>
              {asked.length ? (
                <Txt size={fontSizes.xs} weight="bold" color={colors.muted} style={styles.label}>
                  Yours
                </Txt>
              ) : null}
              <Card padded={false}>{mine.map(row)}</Card>
            </View>
          ) : null}
          {!items.length ? (
            <Card>
              <View style={{ gap: 6 }} testID="organisations-empty">
                <Txt weight="bold">No company or group accounts yet</Txt>
                <Txt size={fontSizes.sm} color={colors.muted}>
                  You can apply for one, or join one when someone asks you to sign for it.
                </Txt>
              </View>
            </Card>
          ) : null}
          <View style={styles.info}>
            <Txt size={fontSizes.sm}>
              A person checks every company and group account, usually within 2 working days. Investing as a company
              or group is not open yet. Opening one now gets its account number ready.
            </Txt>
          </View>
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 14, paddingHorizontal: 16, minHeight: 64 },
  divider: { borderTopWidth: 1, borderTopColor: colors.border },
  label: { textTransform: "uppercase", letterSpacing: 0.6 },
  info: { backgroundColor: colors.surfaceSunken, borderRadius: radii.md, paddingVertical: 12, paddingHorizontal: 14 },
});
