import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";
import { Stack, useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, CheckRow, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Choice } from "@/components/Choice";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { useIdentity } from "@/identity/IdentityProvider";
import { formatAccountNumber } from "@/identity/types";
import { useOrganisations } from "@/organisations/OrganisationsProvider";
import {
  approvalChoices,
  describeOrganisationError,
  DOCUMENT_LABEL,
  DocumentKind,
  KIND_LABEL,
  MAX_SIGNATORIES,
  Organisation,
  OrganisationError,
  PROBLEM_WORDS,
  Signatory,
  signing,
} from "@/organisations/types";
import { Pill, Tone } from "@/organisations/ui";
import { formatDateTime, formatFileSize } from "@/payments/format";
import { chooseReceiptFile } from "@/payments/pickReceipt";
import { checkReceipt, receiptProblemCopy } from "@/payments/receipt";
import { colors, fontSizes, radii } from "@/theme";

type State =
  | { status: "loading" }
  | { status: "ready"; found: Organisation }
  | { status: "gone"; message: string }
  | { status: "error"; message: string };

type Busy = null | "invite" | "answer" | "submit" | "rule" | DocumentKind | `remove:${string}`;

const INVITE_WORDS: Readonly<Record<string, string | undefined>> = {
  no_such_client:
    "We could not find an approved Finvest client with that number. They need their own Finvest account, with its ID check approved.",
  already_a_signatory: "This person already signs for it, or is already asked.",
  too_many_signatories: `At most ${MAX_SIGNATORIES} people can sign.`,
  required: "Say what they are, such as Treasurer.",
};

const DOCUMENT_WORDS: Readonly<Record<string, string | undefined>> = {
  not_a_file_of_that_type: "That file is not what it says it is. Choose the PDF or photo itself.",
  unsupported_content_type: receiptProblemCopy.wrong_type,
  too_large: receiptProblemCopy.too_large,
  empty: receiptProblemCopy.empty,
};

function signatoryPill(person: Signatory, mine: boolean): { label: string; tone: Tone } {
  if (mine) return { label: "You", tone: "lime" };
  switch (person.status) {
    case "joined":
      return { label: "Joined", tone: "grey" };
    case "invited":
      return { label: "Waiting", tone: "amber" };
    default:
      return { label: "Declined", tone: "red" };
  }
}

function signatoryDetail(person: Signatory): string {
  switch (person.status) {
    case "joined":
      return `${person.role}, joined`;
    case "invited":
      return `${person.role}, asked ${formatDateTime(person.invitedAt)}`;
    default:
      return `${person.role}, declined`;
  }
}

/** One company or group account: who signs, the withdrawal rule, its papers, and handing it in. */
export default function OrganisationScreen() {
  const { organisationId } = useLocalSearchParams<{ organisationId: string }>();
  // Keyed, so opening another application starts the screen afresh.
  return <OrganisationView key={organisationId} organisationId={organisationId} />;
}

function OrganisationView({ organisationId }: { organisationId: string }) {
  const organisations = useOrganisations();
  const router = useRouter();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState<Busy>(null);
  const [note, setNote] = useState<{ tone: "neutral" | "danger"; text: string } | null>(null);
  const [adding, setAdding] = useState<{ to: string; role: string } | null>(null);
  const [addErrors, setAddErrors] = useState<Record<string, string>>({});
  const [documentErrors, setDocumentErrors] = useState<Partial<Record<DocumentKind, string>>>({});
  // The reader's own account number, to find their own row among the signatories.
  const { client: identity } = useIdentity();
  const [myNumber, setMyNumber] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    identity.getCheck().then(
      (check) => live && setMyNumber(check?.accountNumber ?? null),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [identity]);

  useFocusEffect(
    useCallback(() => {
      let live = true;
      organisations.get(organisationId).then(
        (found) => live && setState({ status: "ready", found }),
        (caught: unknown) =>
          live &&
          setState(
            caught instanceof OrganisationError && caught.code === "not_found"
              ? { status: "gone", message: describeOrganisationError(caught) }
              : { status: "error", message: describeOrganisationError(caught) },
          ),
      );
      return () => {
        live = false;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [organisations, organisationId, attempt]),
  );

  const run = async (what: Busy, work: () => Promise<Organisation>, done?: string) => {
    setBusy(what);
    setNote(null);
    try {
      const found = await work();
      setState({ status: "ready", found });
      if (done) setNote({ tone: "neutral", text: done });
      return true;
    } catch (caught) {
      setNote({ tone: "danger", text: describeOrganisationError(caught) });
      return false;
    } finally {
      setBusy(null);
    }
  };

  if (state.status !== "ready") {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: "Company or group account" }} />
        <BackButton fallback="/account/organisations" />
        {state.status === "loading" ? (
          <View style={{ marginTop: 60 }}>
            <ActivityIndicator accessibilityLabel="Loading" color={colors.black} />
          </View>
        ) : (
          <View style={{ marginTop: 28, gap: 12 }}>
            <Notice tone="danger">{state.message}</Notice>
            {state.status === "error" ? (
              <Button label="Try again" onPress={() => setAttempt((n) => n + 1)} height={52} />
            ) : (
              <Button label="Back to my accounts" onPress={() => router.replace("/account/organisations")} height={52} />
            )}
          </View>
        )}
      </Screen>
    );
  }

  const found = state.found;
  const company = found.kind === "company";
  const editable = found.isApplicant && found.status === "draft";
  const people = found.signatories.filter((s) => s.status !== "removed");
  const live = signing(found);
  const applicant = found.signatories.find((s) => s.isApplicant);
  const handedIn = new Map(found.documents.map((d) => [d.kind, d]));
  const myRow = (person: Signatory) =>
    found.isApplicant ? person.isApplicant : myNumber !== null && person.accountNumber === myNumber;
  const me = people.find(myRow);

  const invite = async () => {
    if (!adding) return;
    const problems: Record<string, string> = {};
    if (adding.to.replace(/\D/g, "").length < 9) problems.to = "Write their 10 digit account number or their mobile number.";
    if (!adding.role.trim()) problems.role = INVITE_WORDS.required ?? "";
    setAddErrors(problems);
    if (Object.keys(problems).length) return;
    setBusy("invite");
    setNote(null);
    try {
      const updated = await organisations.invite(found.id, adding.to.trim(), adding.role.trim());
      setState({ status: "ready", found: updated });
      setAdding(null);
      const added = updated.signatories.filter((s) => s.status === "invited").at(-1);
      setNote({ tone: "neutral", text: `${added?.name ?? "They"} will be asked in the app to join or decline.` });
    } catch (caught) {
      if (caught instanceof OrganisationError && caught.code === "invalid") {
        const mine: Record<string, string> = {};
        for (const f of caught.fields) mine[f.field] = INVITE_WORDS[f.code] ?? "Check this.";
        setAddErrors(mine);
      } else {
        setNote({ tone: "danger", text: describeOrganisationError(caught) });
      }
    } finally {
      setBusy(null);
    }
  };

  const sendDocument = async (kind: DocumentKind) => {
    setDocumentErrors((current) => ({ ...current, [kind]: undefined }));
    const picked = await chooseReceiptFile();
    if (!picked) return;
    const checked = checkReceipt(picked);
    if (typeof checked === "string") {
      setDocumentErrors((current) => ({ ...current, [kind]: receiptProblemCopy[checked] }));
      return;
    }
    setBusy(kind);
    setNote(null);
    try {
      setState({ status: "ready", found: await organisations.sendDocument(found.id, kind, checked) });
    } catch (caught) {
      const code = caught instanceof OrganisationError && caught.code === "invalid" ? caught.fields[0]?.code : undefined;
      const words = code ? DOCUMENT_WORDS[code] : undefined;
      if (words) setDocumentErrors((current) => ({ ...current, [kind]: words }));
      else setNote({ tone: "danger", text: describeOrganisationError(caught) });
    } finally {
      setBusy(null);
    }
  };

  const submit = () =>
    run("submit", () => organisations.submit(found.id), "Sent. A person at Finvest checks it, usually within 2 working days.");

  const answer = async (join: boolean) => {
    const ok = await run(
      "answer",
      () => organisations.answer(found.id, join),
      join ? `You sign for ${found.name}.` : undefined,
    );
    if (ok && !join) router.replace("/account/organisations");
  };

  const subtitle = (() => {
    if (found.invited) {
      return `${applicant?.name ?? "Someone"} asked you to sign for this ${company ? "company" : "group"} as ${
        me?.role ?? "a signatory"
      }. Join only if you are one of its people.`;
    }
    switch (found.status) {
      case "approved":
        return "Its account is open, with its own Finvest account number.";
      case "rejected":
        return "Finvest could not open this account.";
      case "submitted":
      case "checked":
        return "A person at Finvest is checking it, usually within 2 working days.";
      default:
        return found.isApplicant
          ? "Add the people who can approve money leaving it. Each one joins from their own Finvest app."
          : "The person who applied is still getting it ready.";
    }
  })();

  const footer = found.invited ? (
    <View style={{ gap: 12 }}>
      <Button label="Join" onPress={() => answer(true)} loading={busy === "answer"} testID="organisation-join" />
      <TextLink label="Decline" onPress={() => answer(false)} testID="organisation-decline" />
    </View>
  ) : editable ? (
    <Button
      label="Send for review"
      onPress={submit}
      disabled={found.problems.length > 0}
      loading={busy === "submit"}
      testID="organisation-submit"
    />
  ) : undefined;

  return (
    <Screen signedIn footer={footer}>
      <Stack.Screen options={{ title: found.name }} />
      <BackButton fallback="/account/organisations" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>{company ? "Company account" : "Group account"}</Eyebrow>
        <Title>{found.name}</Title>
        <Txt color={colors.muted}>{subtitle}</Txt>
      </View>

      <View style={{ marginTop: 18, gap: 14 }}>
        {note ? <Notice tone={note.tone}>{note.text}</Notice> : null}
        {editable && found.returnNote ? (
          <View style={styles.returned} testID="organisation-returned">
            <Txt size={fontSizes.sm} weight="bold" color={colors.warningText}>
              Finvest asked you to change something
            </Txt>
            <Txt size={fontSizes.sm} color={colors.warningText}>
              {found.returnNote}
            </Txt>
          </View>
        ) : null}

        {found.status === "approved" && found.accountNumber ? (
          <View style={styles.numberCard} testID="organisation-number">
            <Txt size={fontSizes.xs} weight="bold" color={colors.onBlackMuted} style={styles.caps}>
              Finvest account number
            </Txt>
            <Txt size={32} weight="extrabold" color={colors.white} style={{ letterSpacing: 1 }}>
              {formatAccountNumber(found.accountNumber)}
            </Txt>
            <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
              {KIND_LABEL[found.kind]}, {found.name}
            </Txt>
          </View>
        ) : null}
        {found.status === "approved" ? (
          <Notice tone="neutral">
            Investing as a company or group is not open yet. We will tell every signatory when it is.
          </Notice>
        ) : null}
        {found.status === "rejected" ? (
          <Notice tone="danger">
            We could not open this account. Open Help and we will explain what happens next. You may apply again.
          </Notice>
        ) : null}

        <View style={{ gap: 8 }}>
          <Txt size={fontSizes.xs} weight="bold" color={colors.muted} style={styles.caps}>
            Signatories
          </Txt>
          <Card padded={false}>
            {people.map((person, index) => {
              const pill = signatoryPill(person, myRow(person));
              const removable = editable && !person.isApplicant && person.status !== "declined";
              return (
                <View key={person.clientId} style={[styles.row, index > 0 && styles.divider]} testID={`signatory-${index}`}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <Txt weight="bold">{person.name}</Txt>
                    <Txt size={fontSizes.sm} color={colors.muted}>
                      {signatoryDetail(person)}
                    </Txt>
                    {removable ? (
                      <Pressable
                        accessibilityRole="button"
                        accessibilityLabel={`Take ${person.name} off`}
                        onPress={() =>
                          run(`remove:${person.clientId}`, () => organisations.remove(found.id, person.clientId), `${person.name} is off the list.`)
                        }
                        style={({ pressed }) => [styles.inlineLink, pressed && { opacity: 0.6 }]}
                        testID={`signatory-remove-${index}`}
                      >
                        <Txt size={fontSizes.sm} weight="bold" style={{ textDecorationLine: "underline" }}>
                          {busy === `remove:${person.clientId}` ? "Taking off…" : "Take off"}
                        </Txt>
                      </Pressable>
                    ) : null}
                  </View>
                  <Pill {...pill} />
                </View>
              );
            })}
          </Card>
          {editable && adding ? (
            <Card>
              <View style={{ gap: 14 }} testID="signatory-form">
                <Txt weight="bold">Add a signatory</Txt>
                <TextField
                  label="Their Finvest account number or mobile number"
                  value={adding.to}
                  onChangeText={(to) => setAdding({ ...adding, to })}
                  keyboardType="phone-pad"
                  inputMode="tel"
                  autoComplete="off"
                  hint="They need their own Finvest account with an approved ID check"
                  error={addErrors.to}
                  testID="signatory-to"
                />
                <TextField
                  label="Their role"
                  value={adding.role}
                  onChangeText={(role) => setAdding({ ...adding, role })}
                  autoCapitalize="words"
                  autoComplete="off"
                  hint={company ? "Such as Director or Company secretary" : "Such as Treasurer or Secretary"}
                  error={addErrors.role}
                  testID="signatory-role"
                />
                <Button label="Ask them to sign" onPress={invite} loading={busy === "invite"} height={52} testID="signatory-ask" />
                <TextLink label="Cancel" onPress={() => setAdding(null)} />
              </View>
            </Card>
          ) : editable && live.length < MAX_SIGNATORIES ? (
            <Button
              label="Add a signatory"
              variant="secondary"
              height={52}
              onPress={() => {
                setAddErrors({});
                setAdding({ to: "", role: "" });
              }}
              testID="signatory-add"
            />
          ) : null}
        </View>

        <View style={{ gap: 8 }}>
          <Txt size={fontSizes.xs} weight="bold" color={colors.muted} style={styles.caps}>
            Rule for withdrawals
          </Txt>
          {editable ? (
            <View style={{ gap: 10 }} accessibilityRole="radiogroup" accessibilityLabel="Rule for withdrawals">
              {approvalChoices(found).map((choice) => (
                <Choice
                  key={choice.value}
                  label={choice.label}
                  detail={choice.detail || undefined}
                  selected={found.approvalsNeeded === choice.value}
                  disabled={busy === "rule"}
                  onPress={() =>
                    found.approvalsNeeded !== choice.value &&
                    run("rule", () => organisations.change(found.id, { approvalsNeeded: choice.value }))
                  }
                  testID={`organisation-rule-${choice.value}`}
                />
              ))}
            </View>
          ) : (
            <Card>
              <Txt weight="bold">
                {found.approvalsNeeded === live.length
                  ? `All ${live.length} signatories approve money leaving`
                  : `Any ${found.approvalsNeeded} of ${live.length} signatories approve money leaving`}
              </Txt>
            </Card>
          )}
        </View>

        <View style={{ gap: 8 }}>
          <Txt size={fontSizes.xs} weight="bold" color={colors.muted} style={styles.caps}>
            Documents
          </Txt>
          <Card padded={false}>
            {found.documentsNeeded.map((kind, index) => {
              const done = handedIn.get(kind);
              const label = DOCUMENT_LABEL[kind];
              return (
                <View key={kind} style={[styles.row, index > 0 && styles.divider]} testID={`document-${kind}`}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <Txt weight="bold">{label.title}</Txt>
                    <Txt size={fontSizes.sm} color={colors.muted}>
                      {done ? `${done.contentType === "application/pdf" ? "PDF" : "Photo"}, ${formatFileSize(done.byteSize)}` : label.detail}
                    </Txt>
                    {documentErrors[kind] ? (
                      <Txt size={fontSizes.xs} weight="semibold" color={colors.danger}>
                        {documentErrors[kind]}
                      </Txt>
                    ) : null}
                  </View>
                  {editable ? (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`${done ? "Replace" : "Add"} ${label.title}`}
                      onPress={() => sendDocument(kind)}
                      disabled={busy !== null}
                      style={({ pressed }) => [styles.smallButton, !done && styles.smallButtonOn, pressed && { opacity: 0.7 }]}
                      testID={`document-send-${kind}`}
                    >
                      {busy === kind ? (
                        <ActivityIndicator color={done ? colors.ink : colors.white} accessibilityLabel="Sending" />
                      ) : (
                        <Txt size={fontSizes.sm} weight="bold" color={done ? colors.ink : colors.white}>
                          {done ? "Replace" : "Add"}
                        </Txt>
                      )}
                    </Pressable>
                  ) : (
                    <Txt size={fontSizes.sm} weight="bold">
                      {done ? "Handed in" : "Not yet"}
                    </Txt>
                  )}
                </View>
              );
            })}
          </Card>
        </View>

        {editable ? (
          <View style={{ gap: 8 }} testID="organisation-ready">
            <Txt size={fontSizes.xs} weight="bold" color={colors.muted} style={styles.caps}>
              Before you send it
            </Txt>
            <Card>
              <View style={{ gap: 8 }}>
                {found.problems.length ? (
                  found.problems.map((problem) => (
                    <CheckRow key={problem} met={false}>
                      {PROBLEM_WORDS[problem]}
                    </CheckRow>
                  ))
                ) : (
                  <CheckRow met>Everything is ready to send.</CheckRow>
                )}
              </View>
            </Card>
          </View>
        ) : null}

        {found.invited ? (
          <View style={styles.info}>
            <Txt size={fontSizes.sm}>
              Signing means you may later approve money leaving it. The others see your first name and the first letter
              of your surname, never your details.
            </Txt>
          </View>
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12, paddingHorizontal: 16, minHeight: 64 },
  divider: { borderTopWidth: 1, borderTopColor: colors.border },
  caps: { textTransform: "uppercase", letterSpacing: 0.6 },
  inlineLink: { alignSelf: "flex-start", minHeight: 32, justifyContent: "center" },
  smallButton: {
    minWidth: 84,
    height: 40,
    paddingHorizontal: 14,
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.white,
    alignItems: "center",
    justifyContent: "center",
  },
  smallButtonOn: { backgroundColor: colors.black, borderColor: colors.black },
  numberCard: { backgroundColor: colors.black, borderRadius: 24, paddingVertical: 22, paddingHorizontal: 20, gap: 6, alignItems: "center" },
  returned: { backgroundColor: colors.warningSurface, borderRadius: radii.lg, paddingVertical: 12, paddingHorizontal: 14, gap: 4 },
  info: { backgroundColor: colors.surfaceSunken, borderRadius: radii.md, paddingVertical: 12, paddingHorizontal: 14 },
});
