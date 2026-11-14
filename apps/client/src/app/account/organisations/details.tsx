import { useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { useOrganisations } from "@/organisations/OrganisationsProvider";
import { checkName, checkRegistration } from "@/organisations/rules";
import {
  describeOrganisationError,
  MIN_APPROVALS,
  OrganisationError,
  OrganisationKind,
} from "@/organisations/types";
import { colors } from "@/theme";

const FIELD_WORDS: Readonly<Record<string, string | undefined>> = {
  required: "Write the name.",
  not_a_name: "Use letters, numbers, spaces and & ' . , ( ) - only, with no web address or phone number.",
  not_a_registration_number: "Write the number as on the certificate, such as 80020001234567.",
  already_applied: "An application with this registration number is already open.",
};

/** New account: the organisation's name, its registration number, and the applicant's role. */
export default function OrganisationDetailsScreen() {
  const router = useRouter();
  const organisations = useOrganisations();
  const params = useLocalSearchParams<{ kind?: string }>();
  const kind: OrganisationKind = params.kind === "company" ? "company" : "group";
  const company = kind === "company";
  const [name, setName] = useState("");
  const [registration, setRegistration] = useState("");
  const [role, setRole] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [note, setNote] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const problems: Record<string, string> = {};
    const checked = checkName(name);
    if ("problem" in checked) problems.name = FIELD_WORDS[checked.problem] ?? "Check this.";
    const number = checkRegistration(registration);
    if (number === "invalid") problems.registration_number = FIELD_WORDS.not_a_registration_number ?? "";
    if (company && number === null) problems.registration_number = "A company needs its registration number.";
    if (!role.trim()) problems.role = company ? "Say what you are in the company." : "Say what you are in the group.";
    setErrors(problems);
    setNote(null);
    if (Object.keys(problems).length || "problem" in checked || number === "invalid") return;
    setSaving(true);
    try {
      const created = await organisations.apply({
        kind,
        name: checked.name,
        registrationNumber: number,
        role: role.trim(),
        approvalsNeeded: MIN_APPROVALS[kind],
      });
      router.replace({ pathname: "/account/organisations/[organisationId]", params: { organisationId: created.id } });
    } catch (caught) {
      if (caught instanceof OrganisationError && caught.code === "invalid") {
        const found: Record<string, string> = {};
        for (const f of caught.fields) found[f.field] = FIELD_WORDS[f.code] ?? "Check this.";
        setErrors(found);
      } else {
        setNote(describeOrganisationError(caught));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={<Button label="Continue" onPress={submit} loading={saving} testID="organisation-apply" />}
    >
      <Stack.Screen options={{ title: company ? "Company account" : "Group account" }} />
      <BackButton fallback="/account/organisations/new" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>{company ? "Company account" : "Group account"}</Eyebrow>
        <Title>{company ? "About the company" : "About the group"}</Title>
        <Txt color={colors.muted}>
          {company
            ? "As on its certificate of incorporation. Next you add who signs for it, and its papers."
            : "As the members know it. Next you add who signs for it, and its constitution."}
        </Txt>
      </View>
      <View style={{ marginTop: 22, gap: 14 }}>
        {note ? <Notice tone="danger">{note}</Notice> : null}
        <TextField
          label={company ? "Company name" : "Group name"}
          value={name}
          onChangeText={setName}
          autoCapitalize="words"
          autoComplete="off"
          error={errors.name}
          testID="organisation-name"
        />
        <TextField
          label={company ? "Registration number" : "Registration number, if it has one"}
          value={registration}
          onChangeText={setRegistration}
          autoCapitalize="characters"
          autoComplete="off"
          hint={company ? "From URSB, on the certificate" : "From the district or URSB. Leave it empty if none."}
          error={errors.registration_number}
          testID="organisation-registration"
        />
        <TextField
          label="Your role"
          value={role}
          onChangeText={setRole}
          autoCapitalize="words"
          autoComplete="off"
          hint={company ? "Such as Director" : "Such as Chairperson or Treasurer"}
          error={errors.role}
          testID="organisation-role"
        />
      </View>
    </Screen>
  );
}
