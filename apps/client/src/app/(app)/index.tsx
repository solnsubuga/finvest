import { useCallback, useMemo } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { useActivity } from "@/activity/ActivityProvider";
import { shortDay as activityDay } from "@/activity/build";
import { useAuth } from "@/auth/AuthProvider";
import { useSecondSteps } from "@/auth/useSecondSteps";
import { AppPage } from "@/components/AppPage";
import { Bell } from "@/components/Bell";
import { Notice } from "@/components/Blocks";
import { Icon } from "@/components/Icon";
import { LinkCard } from "@/components/LinkCard";
import { Txt } from "@/components/Txt";
import { ClaimCard } from "@/claim/ClaimCard";
import { claimOpen } from "@/claim/types";
import { useClaim } from "@/claim/useClaim";
import { figure, greeting, monthName, plural, shortDay } from "@/home/format";
import {
  FirstTimeCard,
  HoldingRows,
  InvestmentsTable,
  MaturingNext,
  Panel,
  PanelLine,
  QuickAction,
  QuickActions,
  SetUpList,
  SetUpStep,
  Tile,
  TotalCard,
} from "@/home/Parts";
import { copyFor } from "@/identity/IdCheckCard";
import { useIdentityCheck } from "@/identity/useIdentityCheck";
import { formatAmount } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { useFocusLoaded } from "@/investing/useLoaded";
import { formatMoney } from "@/payments/format";
import { Loading } from "@/payments/Parts";
import { useProfile } from "@/profile/ProfileProvider";
import { colors } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

/** What UGX 100,000 (or the product's minimum, if higher) would earn in the shortest live bill. */
function useFirstExample(enabled: boolean): string | null {
  const investing = useInvesting();
  const [example] = useFocusLoaded(
    useCallback(async () => {
      if (!enabled) return null;
      const bills = (await investing.listProducts())
        .filter((product) => product.status === "live" && product.currency === "UGX" && product.termDays !== null)
        .sort((a, b) => (a.termDays ?? 0) - (b.termDays ?? 0));
      const bill = bills[0];
      if (!bill) return null;
      const amount = { currency: "UGX" as const, minor: Math.max(100_000, bill.minimum.minor) };
      const quote = await investing.quote(bill.code, amount);
      if (!quote.interest || !quote.maturesOn) return null;
      return `${formatAmount(amount)} in a ${bill.termDays} day bill would earn about ${formatAmount(quote.interest)} by ${monthName(quote.maturesOn)}, after tax.`;
    }, [investing, enabled]),
  );
  return example.state === "ready" ? example.value : null;
}

/** The "Get set up" steps. A step whose state could not be read is left out rather than guessed. */
function useSetUpSteps(openGoals: number | null, moving: boolean): SetUpStep[] | null {
  const router = useRouter();
  const profile = useProfile();
  const identity = useIdentityCheck();
  const [questions] = useFocusLoaded(useCallback(() => profile.getRiskProfile(), [profile]));
  const [kin] = useFocusLoaded(useCallback(() => profile.getNextOfKin(), [profile]));

  return useMemo(() => {
    if (identity.status === "loading" || questions.state === "loading" || kin.state === "loading" || openGoals === null) {
      return null;
    }
    const steps: SetUpStep[] = [];
    // A client moving from Finvest 1.0 has the move card instead, which takes in the ID check.
    if (identity.status === "ready" && !moving) {
      const approved = identity.check?.status === "approved";
      // What the check needs next, in the same words the ID check screens use.
      const copy = copyFor(identity.check);
      steps.push({
        key: "id",
        title: copy?.title ?? "Verify your identity",
        detail: copy?.detail ?? "Needed before you invest",
        done: approved,
        onPress: () => router.push("/id-check"),
      });
    }
    if (questions.state === "ready") {
      const count = questions.value.questions.length;
      steps.push({
        key: "questions",
        title: count ? `Answer ${count} questions about your investing` : "Answer questions about your investing",
        detail: questions.value.latest ? "A year has passed. Check your answers again" : "Takes a minute",
        done: !questions.value.due && questions.value.latest !== null,
        onPress: () => router.push("/account/investing-questions"),
      });
    }
    if (kin.state === "ready") {
      steps.push({
        key: "next-of-kin",
        title: "Add next of kin",
        detail: "Who we contact if we cannot reach you",
        done: kin.value.people.length > 0,
        onPress: () => router.push("/account/next-of-kin"),
      });
    }
    steps.push({
      key: "goal",
      title: "Set a goal",
      detail: "School fees, land, an emergency fund",
      done: openGoals > 0,
      onPress: () => router.push("/goals/new"),
    });
    return steps.every((step) => step.done) ? [] : steps;
  }, [identity, questions, kin, openGoals, moving, router]);
}

export default function Home() {
  const router = useRouter();
  const { session } = useAuth();
  const wide = useIsWide();
  const investing = useInvesting();
  const activity = useActivity();
  const steps = useSecondSteps();
  const claim = useClaim();
  // A client moved from Finvest 1.0 is led by their move, which takes in the ID check.
  const moving =
    claim.status === "ready" && claimOpen(claim.claim) ? { claim: claim.claim, check: claim.check } : null;
  // Shown only when we know SMS is the one second step. If the list cannot be
  // loaded, Home stays quiet about it: Sign in and security reports the error.
  const onlySms = steps.status === "ready" && steps.methods.length === 1;
  const name = session?.displayName ?? null;
  const firstName = name?.split(/\s+/)[0];
  const hello = greeting();

  const [portfolio, retry] = useFocusLoaded(useCallback(() => investing.getPortfolio("UGX"), [investing]));
  const [investments] = useFocusLoaded(useCallback(() => (wide ? investing.listInvestments() : Promise.resolve([])), [investing, wide]));
  const [recent] = useFocusLoaded(useCallback(() => (wide ? activity.list() : Promise.resolve({ items: [], since: null })), [activity, wide]));

  const ready = portfolio.state === "ready" ? portfolio.value : null;
  const empty = ready !== null && ready.activeCount === 0 && ready.awaitingPaymentCount === 0;
  const example = useFirstExample(empty);
  const setUp = useSetUpSteps(ready ? ready.openGoals : null, moving !== null);

  const actions: QuickAction[] = [
    { label: "Invest", icon: "plus", onPress: () => router.navigate("/invest"), testID: "quick-invest" },
    { label: "Statements", icon: "statement", onPress: () => router.navigate("/documents"), testID: "quick-statements" },
    { label: "Withdraw", icon: "arrowDown", onPress: () => router.navigate({ pathname: "/portfolio", params: { for: "withdraw" } }), testID: "quick-withdraw" },
    { label: "Send", icon: "send", onPress: () => router.navigate({ pathname: "/portfolio", params: { for: "send" } }), testID: "quick-send" },
  ];

  const notices = (
    <>
      {session?.restricted ? (
        <Notice tone="warning">
          You signed in with a code, so for 24 hours withdrawals only go to accounts you have used before.
        </Notice>
      ) : null}
      {moving ? <ClaimCard claim={moving.claim} check={moving.check} /> : null}
      {onlySms ? (
        <Pressable
          accessibilityRole="link"
          onPress={() => router.push("/security")}
          style={({ pressed }) => [styles.profile, pressed && { opacity: 0.7 }]}
          testID="security-prompt"
        >
          <View style={[styles.avatar, { borderRadius: 14 }]}>
            <Icon name="key" color={colors.lime} />
          </View>
          <View style={{ flex: 1 }}>
            <Txt weight="bold">Make signing in safer</Txt>
            <Txt size={13} color={colors.muted}>
              Add a passkey or an authenticator app, so you do not depend on SMS codes.
            </Txt>
          </View>
          <Icon name="chevron" size={16} strokeWidth={2.2} />
        </Pressable>
      ) : null}
    </>
  );

  const body =
    portfolio.state === "loading" ? (
      <Loading label="Loading your money" />
    ) : portfolio.state === "failed" ? (
      <Failed message={portfolio.message} onRetry={retry} />
    ) : null;

  if (wide) {
    const running = investments.state === "ready" ? investments.value.filter((item) => item.status === "active" || item.status === "awaiting_payment") : [];
    return (
      <AppPage
        title={firstName ? `${hello}, ${firstName}` : hello}
        subtitle="The same account as your phone, on a bigger screen."
        action={
          <View style={styles.wideActions}>
            <WideButton label="Invest" primary onPress={() => router.navigate("/invest")} testID="quick-invest" />
            <WideButton label="Withdraw" onPress={() => router.navigate({ pathname: "/portfolio", params: { for: "withdraw" } })} testID="quick-withdraw" />
            <WideButton label="Statement" onPress={() => router.navigate("/documents")} testID="quick-statements" />
            <Bell />
          </View>
        }
        roomy
        testID="home"
      >
        {notices}
        {body}
        {ready ? (
          <>
            {empty ? (
              <View style={styles.wideRow}>
                <View style={{ flex: 2, minWidth: 320 }}>
                  <FirstTimeCard example={example} onInvest={() => router.navigate("/invest")} />
                </View>
                <View style={{ flex: 3, minWidth: 320 }}>{setUp && setUp.length ? <SetUpList steps={setUp} /> : null}</View>
              </View>
            ) : (
              <>
                <View style={styles.wideRow}>
                  <TotalCard portfolio={ready} wide />
                  <View style={styles.tiles}>
                    <Tile label="Investments" value={figure(ready.outsideGoals.worth)} detail={`${ready.outsideGoals.count} active`} testID="tile-investments" />
                    <Tile
                      label="Goals"
                      value={figure(ready.inGoals.worth)}
                      detail={ready.openGoals ? plural(ready.openGoals, "goal") : "No goals yet"}
                      testID="tile-goals"
                    />
                    <Tile
                      label="Next maturity"
                      value={ready.nextMaturity ? shortDay(ready.nextMaturity.on) : "None"}
                      detail={
                        ready.nextMaturity
                          ? `${ready.nextMaturity.productName}, ${ready.nextMaturity.atMaturity === "pay_out" ? "pays out" : "rolls over"}`
                          : "Nothing running yet"
                      }
                      testID="tile-next-maturity"
                    />
                  </View>
                </View>
                <View style={[styles.wideRow, { alignItems: "flex-start" }]}>
                  <View style={{ flex: 3, minWidth: 480, gap: 16 }}>
                    {running.length ? (
                      <InvestmentsTable investments={running} onOpen={(item) => router.push(`/investment/${item.id}`)} />
                    ) : investments.state === "loading" ? (
                      <Loading label="Loading your investments" />
                    ) : null}
                    {setUp && setUp.length ? <SetUpList steps={setUp} /> : null}
                  </View>
                  <View style={{ flex: 2, minWidth: 300, gap: 16 }}>
                    <Panel title="Recent activity" testID="recent-activity">
                      {recent.state === "ready" ? (
                        recent.value.items.length ? (
                          recent.value.items.slice(0, 4).map((item) => (
                            <PanelLine
                              key={item.key}
                              label={`${item.title}, ${activityDay(item.at)}`}
                              value={`${item.direction === "in" ? "+" : ""}${formatMoney(item.amount).replace(/^[A-Z]+ /, "")}`}
                            />
                          ))
                        ) : (
                          <Txt size={14} color={colors.muted}>
                            Nothing yet.
                          </Txt>
                        )
                      ) : recent.state === "failed" ? (
                        <Txt size={14} color={colors.muted}>
                          We could not load your activity.
                        </Txt>
                      ) : (
                        <Loading label="Loading activity" />
                      )}
                      <WideButton label="See all activity" onPress={() => router.navigate("/activity")} testID="home-all-activity" block />
                    </Panel>
                    <LinkCard
                      icon="repeat"
                      title="Invest automatically"
                      description="The same amount every week or month, from your mobile money"
                      onPress={() => router.push("/autoinvest")}
                      testID="home-autoinvest"
                    />
                  </View>
                </View>
              </>
            )}
          </>
        ) : null}
      </AppPage>
    );
  }

  return (
    <AppPage
      title={name ?? hello}
      header={
        <View style={styles.header}>
          <Pressable
            accessibilityRole="link"
            accessibilityLabel="Profile"
            onPress={() => router.navigate("/profile")}
            style={({ pressed }) => [styles.avatar, pressed && { opacity: 0.8 }]}
            testID="home-profile"
          >
            {name ? (
              <Txt size={15} weight="bold" color={colors.white}>
                {initials(name)}
              </Txt>
            ) : (
              <Icon name="profile" color={colors.white} />
            )}
          </Pressable>
          <View style={{ flex: 1 }}>
            <Txt size={13} color={colors.muted}>
              {empty ? "Welcome" : hello}
            </Txt>
            <Txt size={17} weight="bold" accessibilityRole="header">
              {name ?? "Welcome to Finvest"}
            </Txt>
          </View>
          <Bell />
        </View>
      }
      testID="home"
    >
      {notices}
      {body}
      {ready ? (
        empty ? (
          <>
            <FirstTimeCard example={example} onInvest={() => router.navigate("/invest")} />
            {setUp && setUp.length ? <SetUpList steps={setUp} /> : null}
            <Notice tone="neutral">
              Nothing here yet. Your investments, goals and activity will appear on this screen as soon as you start.
            </Notice>
          </>
        ) : (
          <>
            <TotalCard portfolio={ready} />
            <QuickActions actions={actions} />
            <HoldingRows
              portfolio={ready}
              nextMaturesOn={ready.nextMaturity?.on ?? null}
              onInvestments={() => router.navigate("/portfolio")}
              onGoals={() => router.navigate("/goals")}
            />
            {ready.nextMaturity ? (
              <MaturingNext next={ready.nextMaturity} onPress={() => router.push(`/investment/${ready.nextMaturity?.investmentId}`)} />
            ) : null}
            {ready.awaitingPaymentCount > 0 ? (
              <LinkCard
                icon="activity"
                title={`${plural(ready.awaitingPaymentCount, "investment")} waiting for payment`}
                description="Pay to start earning, or let it lapse"
                onPress={() => router.navigate("/portfolio")}
                testID="home-awaiting"
              />
            ) : null}
            {setUp && setUp.length ? <SetUpList steps={setUp} /> : null}
          </>
        )
      ) : null}
      <LinkCard
        icon="repeat"
        title="Invest automatically"
        description="The same amount every week or month, from your mobile money"
        onPress={() => router.push("/autoinvest")}
        testID="home-autoinvest"
      />
      <LinkCard
        icon="insights"
        title="Insights"
        description="What your money earned, month by month"
        onPress={() => router.navigate("/insights")}
        testID="home-insights"
      />
    </AppPage>
  );
}

function WideButton({
  label,
  onPress,
  primary = false,
  block = false,
  testID,
}: {
  label: string;
  onPress: () => void;
  primary?: boolean;
  block?: boolean;
  testID: string;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [styles.wideButton, primary ? styles.wideLime : styles.wideWhite, block && styles.block, pressed && { opacity: 0.8 }]}
      testID={testID}
    >
      <Txt size={14} weight="bold">
        {label}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", gap: 12 },
  profile: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 14,
  },
  avatar: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.black,
    alignItems: "center",
    justifyContent: "center",
  },
  wideActions: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 },
  wideButton: { height: 44, paddingHorizontal: 18, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  wideLime: { backgroundColor: colors.lime },
  wideWhite: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.borderStrong },
  block: { height: 48, borderRadius: 24, marginTop: 4 },
  wideRow: { flexDirection: "row", flexWrap: "wrap", gap: 16 },
  tiles: { flex: 3, minWidth: 420, flexDirection: "row", flexWrap: "wrap", gap: 16 },
});
