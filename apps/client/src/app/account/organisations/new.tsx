import { useEffect, useState } from "react";
import { View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Choice } from "@/components/Choice";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { useIdentity } from "@/identity/IdentityProvider";
import { formatAccountNumber } from "@/identity/types";
import { OrganisationKind } from "@/organisations/types";
import { colors } from "@/theme";

/** New account: what kind, as on the canvas's "What kind of account?". */
export default function NewOrganisationScreen() {
  const router = useRouter();
  const { client: identity } = useIdentity();
  const [kind, setKind] = useState<OrganisationKind | null>(null);
  const [personal, setPersonal] = useState<{ number: string | null; approved: boolean } | null>(null);

  useEffect(() => {
    let live = true;
    identity.getCheck().then(
      (check) => live && setPersonal({ number: check?.accountNumber ?? null, approved: check?.status === "approved" }),
      () => live && setPersonal(null),
    );
    return () => {
      live = false;
    };
  }, [identity]);

  const blocked = personal !== null && !personal.approved;

  return (
    <Screen
      signedIn
      footer={
        <Button
          label="Continue"
          disabled={!kind || blocked}
          onPress={() =>
            kind && router.push({ pathname: "/account/organisations/details", params: { kind } })
          }
          testID="organisation-kind-continue"
        />
      }
    >
      <Stack.Screen options={{ title: "New account" }} />
      <BackButton fallback="/account/organisations" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>New account</Eyebrow>
        <Title>What kind of account?</Title>
        <Txt color={colors.muted}>Each account gets its own Finvest account number.</Txt>
      </View>
      <View style={{ marginTop: 22, gap: 10 }} accessibilityRole="radiogroup" accessibilityLabel="Kind of account">
        <Choice
          label="Personal"
          detail={
            personal?.number
              ? `Just you. You already have this one, ${formatAccountNumber(personal.number)}`
              : "Just you. You already have this one."
          }
          selected={false}
          disabled
          testID="organisation-kind-personal"
        />
        <Choice
          label="Company"
          detail="A registered business. We ask for its registration papers and who signs for it."
          selected={kind === "company"}
          onPress={() => setKind("company")}
          testID="organisation-kind-company"
        />
        <Choice
          label="Group or SACCO"
          detail="A savings group, family or investment club, with at least two signatories."
          selected={kind === "group"}
          onPress={() => setKind("group")}
          testID="organisation-kind-group"
        />
        {blocked ? (
          <Notice tone="warning">
            Finish your own ID check first. Everyone who signs for a company or group needs an approved Finvest
            account of their own.
          </Notice>
        ) : (
          <Notice tone="neutral">
            Company and group accounts are checked by a person, usually within 2 working days.
          </Notice>
        )}
      </View>
    </Screen>
  );
}
