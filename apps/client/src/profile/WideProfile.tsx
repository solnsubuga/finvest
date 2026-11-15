import { ReactNode, useCallback, useState } from "react";
import { Pressable, StyleSheet, useWindowDimensions, View } from "react-native";
import * as Clipboard from "expo-clipboard";
import { useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164 } from "@/auth/phone";
import { Session } from "@/auth/types";
import { useSecondSteps } from "@/auth/useSecondSteps";
import { useSignOut } from "@/auth/useSignOut";
import { AppPage } from "@/components/AppPage";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon, IconName } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { copyFor } from "@/identity/IdCheckCard";
import { formatAccountNumber, IdentityCheck } from "@/identity/types";
import { useIdentityCheck } from "@/identity/useIdentityCheck";
import { Loaded, useFocusLoaded } from "@/investing/useLoaded";
import { useOrganisations } from "@/organisations/OrganisationsProvider";
import { networkName } from "@/payments/format";
import { usePayouts } from "@/payouts/PayoutsProvider";
import { PayoutAccount } from "@/payouts/types";
import { colors, radii, TWO_COLUMN_BREAKPOINT } from "@/theme";

import { initials } from "./initials";
import { useProfile } from "./ProfileProvider";
import { PROFILE_COPY } from "./questions";
import { useEmailProof } from "./useEmailProof";

/** Below this window width the side column drops under the main one. */
const TWO_COLUMNS = 1180;

type Tone = "ok" | "warn" | "soft";
type Status = { label: string; tone: Tone } | null;

/**
 * Profile on a wide screen (design approved by the owner, 2026-11-15): who
 * the client is in a header card, then their account, money and preferences
 * on the left, and sign-in, help and leaving on the right. Each status is
 * read on its own, so one that cannot be read leaves only its pill out.
 */
export function WideProfile({ session }: { session: Session }) {
  const router = useRouter();
  const { width } = useWindowDimensions();
  const twoColumns = width >= TWO_COLUMNS;
  const identity = useIdentityCheck();
  const check = identity.status === "ready" ? identity.check : undefined;

  return (
    <AppPage
      title="Profile"
      subtitle="Your details, how you sign in, and who we pay and contact."
      roomy
      testID="profile-wide"
    >
      <View style={{ gap: 20 }}>
        <Header session={session} check={check} />
        <View style={twoColumns ? styles.columns : { gap: 20 }}>
          <View style={[{ gap: 20 }, twoColumns && { flex: 1.65 }]}>
            <AccountRows check={check} />
            <Section title="Money">
              <PayoutRow />
            </Section>
            <Section title="Preferences">
              <Row
                icon="bell"
                title="Notifications"
                detail="What we told you, and how we tell you"
                onPress={() => router.push("/notifications")}
                testID="profile-notifications"
              />
              <Row
                icon="download"
                title="Language and data"
                detail="App language, SMS receipts and a copy of your data"
                status={{ label: "English", tone: "soft" }}
                onPress={() => router.push("/account/language")}
                testID="profile-language"
              />
            </Section>
          </View>
          <View style={twoColumns ? { flex: 1, gap: 20 } : styles.sideWrap}>
            <View style={twoColumns ? null : styles.sideHalf}>
              <SecurityCard />
            </View>
            <View style={twoColumns ? null : styles.sideHalf}>
              <Section title="Help and documents">
                <View style={styles.pad}>
                  <Txt weight="bold" size={16}>
                    Need a hand?
                  </Txt>
                  <Txt size={13} color={colors.muted} style={{ marginTop: 2, marginBottom: 14 }}>
                    Quick answers, chat with us, complaints, and your statements and receipts.
                  </Txt>
                  <View style={{ flexDirection: "row", gap: 10 }}>
                    <View style={{ flex: 1 }}>
                      <Button
                        label="Help"
                        icon="message"
                        variant="secondary"
                        height={44}
                        onPress={() => router.navigate("/help")}
                        testID="profile-help"
                      />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Button
                        label="Documents"
                        icon="documents"
                        variant="secondary"
                        height={44}
                        onPress={() => router.navigate("/documents")}
                        testID="profile-documents"
                      />
                    </View>
                  </View>
                </View>
              </Section>
            </View>
            <View style={twoColumns ? null : { width: "100%" }}>
              <LeaveCard />
            </View>
          </View>
        </View>
      </View>
    </AppPage>
  );
}

/** Below this window width the three facts sit one under another, as on a tablet held upright. */

function Header({ session, check }: { session: Session; check: IdentityCheck | null | undefined }) {
  const router = useRouter();
  const stacked = useWindowDimensions().width < TWO_COLUMN_BREAKPOINT;
  const [copied, setCopied] = useState(false);
  const email = useEmailProof();
  const approved = check?.status === "approved";
  const idCopy = check === undefined ? null : copyFor(check);
  const accountNumber = approved ? check.accountNumber : null;

  const copy = async () => {
    if (!accountNumber) return;
    await Clipboard.setStringAsync(accountNumber);
    setCopied(true);
    setTimeout(() => setCopied(false), 2_000);
  };

  const idPill: Status =
    check === undefined
      ? null
      : approved
        ? { label: "ID checked", tone: "ok" }
        : check && check.status !== "draft" && check.status !== "awaiting_client" && check.status !== "rejected"
          ? { label: "ID being checked", tone: "soft" }
          : { label: "ID check needed", tone: "warn" };

  // One prompt at a time: the ID check comes first, because nothing can be invested without it.
  const needsId = check !== undefined && !approved && idCopy !== null;
  const needsEmail = !needsId && Boolean(session.email) && !session.emailVerified;

  return (
    <View style={styles.hero} testID="profile-header">
      <View style={styles.heroTop}>
        <View style={styles.avatar}>
          {session.displayName ? (
            <Txt size={22} weight="extrabold" color={colors.ink}>
              {initials(session.displayName)}
            </Txt>
          ) : (
            <Icon name="profile" size={26} color={colors.ink} />
          )}
        </View>
        <View style={{ flex: 1, gap: 8 }}>
          <Txt size={26} weight="extrabold" color={colors.white} accessibilityRole="header" style={{ letterSpacing: -0.4 }}>
            {session.displayName ?? formatE164(session.phone)}
          </Txt>
          {idPill ? (
            <View style={{ flexDirection: "row" }}>
              <Pill status={idPill} icon={approved ? "check" : undefined} onDark />
            </View>
          ) : null}
        </View>
      </View>
      <View style={[styles.facts, stacked && styles.factsStacked]}>
        <Fact label="Account number" first stacked={stacked}>
          {accountNumber ? (
            <View style={styles.factValue}>
              <Txt size={17} weight="bold" color={colors.white} style={{ letterSpacing: 1 }} testID="profile-account-number">
                {formatAccountNumber(accountNumber)}
              </Txt>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={copied ? "Copied" : "Copy account number"}
                onPress={copy}
                hitSlop={8}
                testID="profile-copy-account-number"
              >
                {copied ? (
                  <Txt size={12} weight="bold" color={colors.lime}>
                    Copied
                  </Txt>
                ) : (
                  <Icon name="copy" size={16} color={colors.onBlackMuted} />
                )}
              </Pressable>
            </View>
          ) : (
            <Txt size={15} weight="semibold" color={colors.onBlackMuted}>
              {check === undefined ? " " : "After your ID check"}
            </Txt>
          )}
        </Fact>
        <Fact label="Mobile number" stacked={stacked}>
          <Txt size={17} weight="bold" color={colors.white}>
            {formatE164(session.phone)}
          </Txt>
        </Fact>
        <Fact label="Email" grow={1.4} stacked={stacked}>
          {session.email ? (
            <View style={[styles.factValue, { flexWrap: "wrap" }]} testID="profile-email">
              <Txt size={17} weight="bold" color={colors.white} style={{ flexShrink: 1 }}>
                {session.email}
              </Txt>
              <Pill
                status={session.emailVerified ? { label: "Proved", tone: "ok" } : { label: "Not proved yet", tone: "warn" }}
                onDark
              />
            </View>
          ) : (
            <View style={{ gap: 2 }} testID="profile-email">
              <Txt size={15} weight="semibold" color={colors.onBlackMuted}>
                No email yet
              </Txt>
              <Pressable accessibilityRole="link" onPress={() => router.navigate("/help")}>
                <Txt size={13} weight="bold" color={colors.lime} style={{ textDecorationLine: "underline" }}>
                  Ask us in Help to add one
                </Txt>
              </Pressable>
            </View>
          )}
        </Fact>
      </View>
      {needsId && idCopy ? (
        <Prompt
          icon="shield"
          title={idCopy.title}
          detail={idCopy.detail}
          action={
            <Button
              label={check === null ? "Start ID check" : "Open ID check"}
              variant="lime"
              height={44}
              onPress={() => router.push("/id-check")}
              testID="profile-start-id-check"
            />
          }
        />
      ) : null}
      {needsEmail ? (
        <View style={{ gap: 10 }}>
          <Prompt
            icon="mail"
            title="Prove your email"
            detail="We email statements and receipts only to an address you have proved is yours."
            action={
              <Button
                label={email.note?.tone === "neutral" ? "Send another link" : "Email me a link"}
                variant="lime"
                height={44}
                onPress={email.sendLink}
                loading={email.sending}
                testID="send-email-proof"
              />
            }
          />
          {email.note ? <Notice tone={email.note.tone}>{email.note.text}</Notice> : null}
        </View>
      ) : null}
    </View>
  );
}

function AccountRows({ check }: { check: IdentityCheck | null | undefined }) {
  const router = useRouter();
  const profile = useProfile();
  const organisations = useOrganisations();
  const [kin] = useFocusLoaded(useCallback(() => profile.getNextOfKin(), [profile]));
  const [risk] = useFocusLoaded(useCallback(() => profile.getRiskProfile(), [profile]));
  const [orgs] = useFocusLoaded(useCallback(() => organisations.list(), [organisations]));

  const idStatus: Status =
    check === undefined
      ? null
      : check?.status === "approved"
        ? { label: "Done", tone: "ok" }
        : check && !["draft", "awaiting_client", "rejected"].includes(check.status)
          ? { label: "Being checked", tone: "soft" }
          : { label: "To do", tone: "warn" };

  const kinStatus = whenReady(kin, (value) =>
    value.people.length === 0
      ? { label: "Not added", tone: "warn" }
      : { label: value.people.length === 1 ? "1 person" : `${value.people.length} people`, tone: "soft" },
  );
  const riskStatus = whenReady(risk, (value) =>
    !value.latest
      ? { label: "Not answered", tone: "warn" }
      : value.due
        ? { label: "Due again", tone: "warn" }
        : { label: PROFILE_COPY[value.latest.profile].name, tone: "soft" },
  );
  const orgStatus = whenReady(orgs, (value) =>
    value.length === 0 ? null : { label: value.length === 1 ? "1 account" : `${value.length} accounts`, tone: "soft" },
  );

  return (
    <Section title="Your account">
      <Row
        icon="shield"
        title="ID check and account number"
        detail="Confirm who you are, and see your account number"
        status={idStatus}
        onPress={() => router.push("/id-check")}
        testID="profile-id-check"
      />
      <Row
        icon="profile"
        title="Next of kin"
        detail="Who we contact if something happens to you"
        status={kinStatus}
        onPress={() => router.push("/account/next-of-kin")}
        testID="profile-next-of-kin"
      />
      <Row
        icon="insights"
        title="About your investing"
        detail="Four questions so we suggest what suits you"
        status={riskStatus}
        onPress={() => router.push("/account/investing-questions")}
        testID="profile-investing-questions"
      />
      <Row
        icon="portfolio"
        title="Company and group accounts"
        detail="Open one for a business, savings group or SACCO"
        status={orgStatus}
        onPress={() => router.push("/account/organisations")}
        testID="profile-organisations"
      />
    </Section>
  );
}

/** "MTN and 1 bank": the approved places we can pay, by network and bank count. */
export function payoutSummary(accounts: PayoutAccount[]): string | null {
  const approved = accounts.filter((account) => account.status === "approved");
  if (approved.length === 0) return null;
  const networks = [
    ...new Set(
      approved
        .filter((account) => account.kind === "mobile_money")
        .map((account) => (account.network ? networkName[account.network] : "Mobile money")),
    ),
  ];
  const banks = approved.filter((account) => account.kind !== "mobile_money").length;
  const parts = [...networks];
  if (banks > 0) parts.push(banks === 1 ? "1 bank" : `${banks} banks`);
  return parts.join(" and ");
}

function PayoutRow() {
  const router = useRouter();
  const payouts = usePayouts();
  const [accounts] = useFocusLoaded(useCallback(() => payouts.listAccounts(), [payouts]));
  const status = whenReady(accounts, (value) => {
    const summary = payoutSummary(value);
    return summary ? { label: summary, tone: "soft" } : { label: "None yet", tone: "soft" };
  });
  return (
    <Row
      icon="send"
      title="Payout accounts"
      detail="Where we pay you, and your payouts"
      status={status}
      onPress={() => router.push("/payouts")}
      testID="open-payout-accounts"
    />
  );
}

function SecurityCard() {
  const router = useRouter();
  const { client } = useAuth();
  const steps = useSecondSteps();
  const [devices] = useFocusLoaded(useCallback(() => client.listDevices(), [client]));
  const methods = steps.status === "ready" ? steps.methods : null;
  const onOff = (on: boolean): Status => ({ label: on ? "On" : "Off", tone: on ? "ok" : "soft" });
  const strong = methods ? methods.includes("passkey") || methods.includes("authenticator") : null;

  return (
    <Section title="Sign in and security">
      <View style={styles.pad} testID="profile-security">
        <SecurityLine label="Passkey" status={methods ? onOff(methods.includes("passkey")) : null} />
        <SecurityLine divided label="Authenticator app" status={methods ? onOff(methods.includes("authenticator")) : null} />
        <SecurityLine divided label="SMS codes" status={methods ? onOff(methods.includes("code")) : null} />
        <View style={[styles.securityLine, styles.securityDivider]}>
          <Txt size={14} weight="semibold">
            Signed in on
          </Txt>
          {devices.state === "ready" ? (
            <Txt size={14} weight="bold">
              {devices.value.length === 1 ? "1 device" : `${devices.value.length} devices`}
            </Txt>
          ) : null}
        </View>
        {strong !== null ? (
          <Txt size={13} color={colors.muted} style={{ marginTop: 8 }}>
            {strong
              ? "A new device asks for your passkey or authenticator code."
              : "Add a passkey or an authenticator app so you do not depend on SMS codes."}
          </Txt>
        ) : null}
        <View style={{ marginTop: 14 }}>
          <Button
            label="Manage sign in and security"
            icon="key"
            variant="secondary"
            height={44}
            onPress={() => router.push("/security")}
            testID="open-security"
          />
        </View>
      </View>
    </Section>
  );
}

function LeaveCard() {
  const router = useRouter();
  const { signOut, busy } = useSignOut();
  return (
    <Section title="Leave or pause">
      <View style={[styles.pad, { gap: 10 }]}>
        <Button
          label="Log out of this device"
          icon="logout"
          variant="secondary"
          height={44}
          onPress={signOut}
          loading={busy}
          testID="sign-out"
        />
        <Button
          label="Freeze my account"
          icon="lock"
          variant="secondary"
          height={44}
          onPress={() => router.push("/account/freeze")}
          testID="profile-freeze"
        />
        <View style={{ alignSelf: "center", paddingTop: 4 }}>
          <TextLink label="Close my account" onPress={() => router.push("/account/close")} testID="profile-close" />
        </View>
      </View>
    </Section>
  );
}

function whenReady<T>(loaded: Loaded<T>, status: (value: T) => Status): Status {
  return loaded.state === "ready" ? status(loaded.value) : null;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <View style={{ gap: 8 }}>
      <Txt size={12} weight="bold" color={colors.muted} accessibilityRole="header" style={styles.sectionTitle}>
        {title}
      </Txt>
      <View style={styles.card}>{children}</View>
    </View>
  );
}

type RowProps = {
  icon: IconName;
  title: string;
  detail: string;
  status?: Status;
  onPress: () => void;
  testID: string;
};

function Row({ icon, title, detail, status = null, onPress, testID }: RowProps) {
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={status ? `${title}, ${status.label}` : title}
      onPress={onPress}
      style={({ pressed, hovered }: { pressed: boolean; hovered?: boolean }) => [
        styles.row,
        (pressed || hovered) && { backgroundColor: colors.paper },
      ]}
      testID={testID}
    >
      <View style={styles.rowIcon}>
        <Icon name={icon} size={18} color={colors.black} />
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Txt weight="bold">{title}</Txt>
        <Txt size={13} color={colors.muted}>
          {detail}
        </Txt>
      </View>
      {status ? <Pill status={status} testID={`${testID}-status`} /> : null}
      <Icon name="chevron" size={16} strokeWidth={2.2} />
    </Pressable>
  );
}

function SecurityLine({ label, status, divided = false }: { label: string; status: Status; divided?: boolean }) {
  return (
    <View style={[styles.securityLine, divided && styles.securityDivider]}>
      <Txt size={14} weight="semibold">
        {label}
      </Txt>
      {status ? <Pill status={status} /> : null}
    </View>
  );
}

function Fact({
  label,
  first = false,
  grow = 1,
  stacked = false,
  children,
}: {
  label: string;
  first?: boolean;
  grow?: number;
  stacked?: boolean;
  children: ReactNode;
}) {
  return (
    <View style={[styles.fact, stacked ? styles.factStacked : { flex: grow }, first && styles.factFirst]}>
      <Txt size={13} color={colors.onBlackMuted}>
        {label}
      </Txt>
      {children}
    </View>
  );
}

function Prompt({ icon, title, detail, action }: { icon: IconName; title: string; detail: string; action: ReactNode }) {
  return (
    <View style={styles.prompt}>
      <Icon name={icon} size={22} color={colors.lime} />
      <View style={{ flex: 1, gap: 2 }}>
        <Txt size={15} weight="bold" color={colors.white}>
          {title}
        </Txt>
        <Txt size={14} color={colors.onBlackMuted}>
          {detail}
        </Txt>
      </View>
      <View style={{ minWidth: 180 }}>{action}</View>
    </View>
  );
}

const pillColours: Record<Tone, { background: string; text: string }> = {
  ok: { background: colors.limeSurface, text: colors.limeText },
  warn: { background: colors.warningSurface, text: colors.warningText },
  soft: { background: colors.surfaceSunken, text: colors.ink },
};

function Pill({
  status,
  icon,
  onDark = false,
  testID,
}: {
  status: NonNullable<Status>;
  icon?: IconName;
  onDark?: boolean;
  testID?: string;
}) {
  // On the black header a done pill is full lime, the brand's "all good".
  const fill = onDark && status.tone === "ok" ? { background: colors.lime, text: colors.ink } : pillColours[status.tone];
  return (
    <View style={[styles.pill, { backgroundColor: fill.background }]} testID={testID}>
      {icon ? <Icon name={icon} size={13} color={fill.text} strokeWidth={3} /> : null}
      <Txt size={12} weight="bold" color={fill.text}>
        {status.label}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  columns: { flexDirection: "row", gap: 20, alignItems: "flex-start" },
  sideWrap: { flexDirection: "row", flexWrap: "wrap", gap: 20, alignItems: "flex-start" },
  sideHalf: { flexGrow: 1, flexBasis: 300 },
  hero: { backgroundColor: colors.black, borderRadius: radii.xl, padding: 28, gap: 22 },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 18 },
  avatar: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
  },
  facts: {
    flexDirection: "row",
    borderTopWidth: 1,
    borderTopColor: colors.onBlackDivider,
    paddingTop: 20,
  },
  fact: {
    gap: 4,
    paddingHorizontal: 20,
    borderLeftWidth: 1,
    borderLeftColor: colors.onBlackDivider,
    minWidth: 0,
  },
  factsStacked: { flexDirection: "column", gap: 14 },
  factStacked: { paddingHorizontal: 0, borderLeftWidth: 0 },
  factFirst: { paddingLeft: 0, borderLeftWidth: 0 },
  factValue: { flexDirection: "row", alignItems: "center", gap: 8 },
  prompt: {
    backgroundColor: colors.onBlackSurface,
    borderRadius: 14,
    paddingVertical: 14,
    paddingHorizontal: 16,
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
  },
  sectionTitle: { textTransform: "uppercase", letterSpacing: 0.6, marginLeft: 4 },
  card: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.xl,
    overflow: "hidden",
  },
  pad: { padding: 20 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 16,
    minHeight: 68,
    borderTopWidth: 1,
    borderTopColor: colors.divider,
    marginTop: -1,
  },
  rowIcon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
  securityLine: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 11,
  },
  securityDivider: { borderTopWidth: 1, borderTopColor: colors.divider },
  pill: {
    borderRadius: radii.pill,
    paddingVertical: 3,
    paddingHorizontal: 10,
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
  },
});
