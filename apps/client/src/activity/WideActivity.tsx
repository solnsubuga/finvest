import { useState } from "react";
import { Pressable, StyleSheet, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import { Card } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { DataTable, Pill } from "@/components/DataTable";
import { Txt } from "@/components/Txt";
import { Panel, PanelLine } from "@/home/Parts";
import { formatDateTime, formatMoney, formatTime } from "@/payments/format";
import { colors, fontFor } from "@/theme";

import { shortDay } from "./build";
import { matches, withinDays } from "./table";
import { ActivityItem, ActivityKind, ActivityStatus } from "./types";

export type WideFilter = "all" | ActivityKind;

const FILTERS: { value: WideFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "payment", label: "Paid in" },
  { value: "payout", label: "Paid out" },
  { value: "interest", label: "Interest" },
];

const TONE: Record<ActivityStatus, "good" | "waiting" | "bad"> = { done: "good", under_way: "waiting", failed: "bad" };

const COLUMNS = [
  { key: "when", label: "When", flex: 0.9 },
  { key: "what", label: "What", flex: 1.6 },
  { key: "for", label: "For", flex: 1.2 },
  { key: "amount", label: "Amount", flex: 1 },
  { key: "status", label: "Status", flex: 1.1 },
];

/** The rows the wide filters and search leave, newest first as they came. */
export function shownRows(items: ActivityItem[], filter: WideFilter, recent: boolean, query: string, nowMs: number): ActivityItem[] {
  return items.filter(
    (item) => (filter === "all" || item.kind === filter) && (!recent || withinDays(item, nowMs, 30)) && matches(item, query),
  );
}

function signed(item: ActivityItem): string {
  const figure = formatMoney(item.amount).replace(/^[A-Z]+ /, "");
  return item.direction === "in" ? `+${figure}` : figure;
}

/**
 * Activity on a wide screen: filters and a search over a table, with the
 * chosen row's details beside it. The parent owns the filters, so its CSV
 * download holds exactly the rows shown.
 */
export function WideActivity({
  items,
  filter,
  onFilter,
  recent,
  onRecent,
  query,
  onQuery,
  shown,
}: {
  items: ActivityItem[];
  filter: WideFilter;
  onFilter: (filter: WideFilter) => void;
  recent: boolean;
  onRecent: (recent: boolean) => void;
  query: string;
  onQuery: (query: string) => void;
  shown: ActivityItem[];
}) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected = shown.find((item) => item.key === selectedKey) ?? shown[0] ?? null;

  return (
    <View style={{ gap: 16 }}>
      <View style={styles.filters}>
        {FILTERS.map((option) => (
          <Chip key={option.value} label={option.label} active={option.value === filter} onPress={() => onFilter(option.value)} testID={`filter-${option.value}`} />
        ))}
        <Chip label="Last 30 days" active={recent} onPress={() => onRecent(!recent)} testID="filter-recent" />
        <TextInput
          accessibilityLabel="Search activity"
          placeholder="Search by what, how or amount"
          placeholderTextColor={colors.muted}
          value={query}
          onChangeText={onQuery}
          style={styles.search}
          testID="activity-search"
        />
      </View>
      <View style={styles.columns}>
        <View style={{ flex: 3, minWidth: 480 }}>
          {shown.length === 0 ? (
            <Card>
              <Txt color={colors.muted} testID="activity-empty">
                {items.length === 0
                  ? "Nothing here yet. Your payments, interest and payouts will be listed here as they happen."
                  : "Nothing matches. Try another search, or clear the filters."}
              </Txt>
            </Card>
          ) : (
            <DataTable
              columns={COLUMNS}
              testID="activity-table"
              rows={shown.map((item) => ({
                key: item.key,
                label: `${item.title}, ${signed(item)}, ${item.statusLabel}`,
                selected: item.key === selected?.key,
                onPress: () => setSelectedKey(item.key),
                testID: `activity-${item.key}`,
                cells: {
                  when: `${shortDay(item.at)}, ${formatTime(item.at)}`,
                  what: item.title,
                  for: item.detail,
                  amount: (
                    <Txt
                      size={14}
                      weight="bold"
                      color={item.status === "failed" ? colors.muted : item.direction === "in" ? colors.success : colors.ink}
                      style={[{ fontVariant: ["tabular-nums"] }, item.status === "failed" && { textDecorationLine: "line-through" }]}
                    >
                      {signed(item)}
                    </Txt>
                  ),
                  status: <Pill label={item.statusLabel} tone={TONE[item.status]} />,
                },
              }))}
            />
          )}
        </View>
        <View style={{ flex: 2, minWidth: 300 }}>{selected ? <Details item={selected} /> : null}</View>
      </View>
    </View>
  );
}

function Details({ item }: { item: ActivityItem }) {
  const router = useRouter();
  return (
    <Panel title={item.status === "done" ? "Receipt" : "Details"} testID="activity-detail">
      <Pill label={item.statusLabel} tone={TONE[item.status]} />
      <Txt size={30} weight="extrabold" lineHeight={38} style={{ letterSpacing: -0.8 }}>
        {formatMoney(item.amount)}
      </Txt>
      <Txt size={13} color={colors.muted}>
        {item.title}
      </Txt>
      <PanelLine label="Date and time" value={formatDateTime(item.at)} />
      <PanelLine label={item.kind === "payment" ? "Paid with" : item.kind === "payout" ? "Paid" : "How"} value={item.detail} />
      <PanelLine label="Status" value={item.statusLabel} />
      <View style={{ gap: 8, marginTop: 6 }}>
        {item.investmentId ? (
          <Button
            label="Open the investment"
            height={48}
            onPress={() => router.push({ pathname: "/investment/[id]", params: { id: item.investmentId as string } })}
            testID="detail-investment"
          />
        ) : null}
        <Button label="Something wrong with this?" variant="secondary" height={48} onPress={() => router.navigate("/help")} testID="detail-help" />
      </View>
    </Panel>
  );
}

function Chip({ label, active, onPress, testID }: { label: string; active: boolean; onPress: () => void; testID: string }) {
  return (
    <Pressable
      accessibilityRole="button"
      aria-pressed={active}
      onPress={onPress}
      style={({ pressed }) => [styles.chip, active && styles.chipActive, pressed && { opacity: 0.8 }]}
      testID={testID}
    >
      <Txt size={13} weight="bold" color={active ? colors.white : colors.ink}>
        {label}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  filters: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 },
  chip: {
    height: 44,
    paddingHorizontal: 16,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    justifyContent: "center",
  },
  chipActive: { backgroundColor: colors.black, borderColor: colors.black },
  search: {
    flexGrow: 1,
    flexBasis: 220,
    height: 44,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    paddingHorizontal: 16,
    fontFamily: fontFor("medium"),
    fontSize: 14,
    color: colors.ink,
  },
  columns: { flexDirection: "row", flexWrap: "wrap", gap: 20, alignItems: "flex-start" },
});
