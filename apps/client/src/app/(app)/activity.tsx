import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";

import { byMonth, longDay, shortDay } from "@/activity/build";
import { useActivity } from "@/activity/ActivityProvider";
import { saveCsv } from "@/activity/saveCsv";
import { toCsv } from "@/activity/table";
import { shownRows, WideActivity, WideFilter } from "@/activity/WideActivity";
import { ActivityItem, ActivityKind } from "@/activity/types";
import { AppPage } from "@/components/AppPage";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon, IconName } from "@/components/Icon";
import { PillButton } from "@/components/PillButton";
import { Eyebrow, Txt } from "@/components/Txt";
import { formatMoney } from "@/payments/format";
import { Loading } from "@/payments/Parts";
import { colors, COLUMN_WIDTH, fontSizes, radii } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

type State = { status: "loading" } | { status: "error" } | { status: "ready"; items: ActivityItem[]; since: string | null; /** When it was read, for "Last 30 days". */ readAt: number };

type Filter = "all" | ActivityKind;

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "payment", label: "Payments" },
  { value: "interest", label: "Interest" },
  { value: "payout", label: "Payouts" },
];

const ICONS: Record<ActivityKind, IconName> = { payment: "send", interest: "insights", payout: "download" };

const EMPTY: Record<Filter, string> = {
  all: "Nothing here yet. Your payments, interest and payouts will be listed here as they happen.",
  payment: "No payments yet. When you pay for an investment it is listed here.",
  interest: "No interest paid yet. Interest is listed here when an investment matures.",
  payout: "No payouts yet. Money we send to your mobile money or bank account is listed here.",
};

/** Activity: payments, interest and payouts in one list, newest first, by month. */
export default function Activity() {
  const client = useActivity();
  const router = useRouter();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const wide = useIsWide();
  // The wide table's own filters, so the CSV holds exactly the rows it shows.
  const [wideFilter, setWideFilter] = useState<WideFilter>("all");
  const [recent, setRecent] = useState(false);
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState<"idle" | "saving" | "failed">("idle");

  useFocusEffect(
    useCallback(() => {
      let live = true;
      client.list().then(
        ({ items, since }) => live && setState({ status: "ready", items, since, readAt: Date.now() }),
        () => live && setState({ status: "error" }),
      );
      return () => {
        live = false;
      };
      // `attempt` is here so "Try again" asks once more.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [client, attempt]),
  );

  const shown = state.status === "ready" ? state.items.filter((item) => filter === "all" || item.kind === filter) : [];
  const wideShown = state.status === "ready" ? shownRows(state.items, wideFilter, recent, query, state.readAt) : [];

  const download = async () => {
    setSaving("saving");
    try {
      await saveCsv(`finvest-activity-${new Date().toISOString().slice(0, 10)}.csv`, toCsv(wideShown));
      setSaving("idle");
    } catch {
      setSaving("failed");
    }
  };

  return (
    <AppPage
      title="Activity"
      subtitle={wide ? "Everything that has happened on your account. Choose a row to see its details." : "Payments, interest and payouts"}
      roomy={wide}
      action={
        wide ? (
          <View style={styles.actions}>
            <PillButton
              label="Download as CSV"
              primary
              loading={saving === "saving"}
              disabled={state.status !== "ready" || wideShown.length === 0}
              onPress={download}
              testID="activity-csv"
            />
            <PillButton label="Get statement" onPress={() => router.navigate("/documents")} testID="activity-statement" />
          </View>
        ) : undefined
      }
    >
      <View style={wide ? { gap: 16 } : { maxWidth: COLUMN_WIDTH, gap: 16 }}>
        {state.status === "loading" ? <Loading label="Loading your activity" /> : null}
        {state.status === "error" ? (
          <View style={{ gap: 12 }}>
            <Notice tone="danger">We could not load your activity. Check your connection and try again.</Notice>
            <Button
              label="Try again"
              height={52}
              onPress={() => {
                setState({ status: "loading" });
                setAttempt((value) => value + 1);
              }}
              testID="activity-retry"
            />
          </View>
        ) : null}
        {saving === "failed" ? <Notice tone="danger">We could not save the file. Try again.</Notice> : null}
        {state.status === "ready" && wide ? (
          <>
            <WideActivity
              items={state.items}
              filter={wideFilter}
              onFilter={setWideFilter}
              recent={recent}
              onRecent={setRecent}
              query={query}
              onQuery={setQuery}
              shown={wideShown}
            />
            {state.since ? (
              <Txt size={fontSizes.sm} color={colors.muted} testID="activity-since">
                {`This list goes back to ${longDay(state.since)}. For anything older, get a statement.`}
              </Txt>
            ) : null}
          </>
        ) : null}
        {state.status === "ready" && !wide ? (
          <>
            <View style={styles.filters} accessibilityRole="tablist">
              {FILTERS.map((option) => {
                const active = option.value === filter;
                return (
                  <Pressable
                    key={option.value}
                    accessibilityRole="tab"
                    aria-selected={active}
                    onPress={() => setFilter(option.value)}
                    style={[styles.chip, active && styles.chipActive]}
                    testID={`filter-${option.value}`}
                  >
                    <Txt size={fontSizes.sm} weight="bold" color={active ? colors.white : colors.ink}>
                      {option.label}
                    </Txt>
                  </Pressable>
                );
              })}
            </View>
            {shown.length === 0 ? (
              <Card>
                <Txt color={colors.muted} testID="activity-empty">
                  {EMPTY[filter]}
                </Txt>
              </Card>
            ) : (
              byMonth(shown).map((group) => (
                <View key={group.month} style={{ gap: 8 }}>
                  <Eyebrow>{group.month}</Eyebrow>
                  <Card padded={false}>
                    {group.items.map((item, index) => (
                      <Row
                        key={item.key}
                        item={item}
                        first={index === 0}
                        onPress={
                          item.investmentId
                            ? () => router.push({ pathname: "/investment/[id]", params: { id: item.investmentId as string } })
                            : undefined
                        }
                      />
                    ))}
                  </Card>
                </View>
              ))
            )}
            {state.since ? (
              <Txt size={fontSizes.sm} color={colors.muted} testID="activity-since">
                {`This list goes back to ${longDay(state.since)}. For anything older, ask for a statement in Documents.`}
              </Txt>
            ) : null}
          </>
        ) : null}
      </View>
    </AppPage>
  );
}

function Row({ item, first, onPress }: { item: ActivityItem; first: boolean; onPress?: () => void }) {
  const tone =
    item.status === "failed" ? colors.danger : item.status === "under_way" ? colors.warningText : colors.muted;
  const amount = `${item.direction === "in" ? "+" : ""}${formatMoney(item.amount)}`;
  const content = (
    <>
      <View style={[styles.icon, item.kind === "interest" && { backgroundColor: colors.lime }]}>
        <Icon name={ICONS[item.kind]} size={18} color={colors.black} />
      </View>
      <View style={{ flex: 1, gap: 2 }}>
        <Txt weight="bold">{item.title}</Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {`${shortDay(item.at)} · ${item.detail}`}
        </Txt>
      </View>
      <View style={{ alignItems: "flex-end", gap: 2 }}>
        <Txt
          weight="bold"
          color={item.status === "failed" ? colors.muted : item.direction === "in" ? colors.success : colors.ink}
          style={item.status === "failed" ? { textDecorationLine: "line-through" } : undefined}
        >
          {amount}
        </Txt>
        <Txt size={12} weight="semibold" color={tone}>
          {item.statusLabel}
        </Txt>
      </View>
    </>
  );
  const style = [styles.row, !first && styles.divider];
  return onPress ? (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={`${item.title}, ${amount}, ${item.statusLabel}`}
      onPress={onPress}
      style={({ pressed }) => [...style, pressed && { opacity: 0.7 }]}
      testID={`activity-${item.key}`}
    >
      {content}
    </Pressable>
  ) : (
    <View style={style} accessible accessibilityLabel={`${item.title}, ${amount}, ${item.statusLabel}`} testID={`activity-${item.key}`}>
      {content}
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  filters: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    paddingVertical: 8,
    paddingHorizontal: 14,
    // A 44 point touch target, as every control in the app has.
    minHeight: 44,
    justifyContent: "center",
  },
  chipActive: { backgroundColor: colors.black, borderColor: colors.black },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, minHeight: 68 },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  icon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
});
