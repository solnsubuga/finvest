import { ReactNode } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { colors } from "@/theme";

import { Txt } from "./Txt";

export type Column = { key: string; label: string; /** Relative width. The default is 1. */ flex?: number };

export type Row = {
  key: string;
  /** Each column's cell, by the column's key. A string is set as plain text. */
  cells: Record<string, ReactNode>;
  /** What a screen reader says for the whole row. */
  label?: string;
  selected?: boolean;
  onPress?: () => void;
  testID?: string;
};

/** A table for the wide layouts: a muted heading row, then hairline rows, any of which may be chosen. */
export function DataTable({ columns, rows, testID }: { columns: Column[]; rows: Row[]; testID?: string }) {
  const cell = (column: Column, value: ReactNode, first: boolean, header = false) => (
    <View key={column.key} style={[styles.cell, { flex: column.flex ?? 1 }]}>
      {typeof value === "string" || typeof value === "number" ? (
        <Txt
          size={header ? 12 : 14}
          weight={header || first ? "bold" : "regular"}
          color={header ? colors.muted : colors.ink}
          style={header ? undefined : styles.tabular}
        >
          {value}
        </Txt>
      ) : (
        value
      )}
    </View>
  );

  return (
    <View style={styles.table} testID={testID}>
      <View style={styles.row} accessibilityRole="header">
        {columns.map((column, index) => cell(column, column.label, index === 0, true))}
      </View>
      {rows.map((row) => {
        const content = columns.map((column, index) => cell(column, row.cells[column.key] ?? "", index === 0));
        return row.onPress ? (
          <Pressable
            key={row.key}
            accessibilityRole="button"
            accessibilityLabel={row.label}
            aria-selected={row.selected}
            onPress={row.onPress}
            style={({ pressed, hovered }: { pressed: boolean; hovered?: boolean }) => [
              styles.row,
              styles.divider,
              (row.selected || hovered) && { backgroundColor: colors.surfaceSunken },
              pressed && { opacity: 0.8 },
            ]}
            testID={row.testID}
          >
            {content}
          </Pressable>
        ) : (
          <View key={row.key} style={[styles.row, styles.divider]} accessible={!!row.label} accessibilityLabel={row.label} testID={row.testID}>
            {content}
          </View>
        );
      })}
    </View>
  );
}

/** A small rounded status label for a table cell. */
export function Pill({ label, tone }: { label: string; tone: "good" | "waiting" | "bad" | "plain" }) {
  const look = {
    good: { backgroundColor: colors.limeSurface, color: colors.limeText },
    waiting: { backgroundColor: colors.warningSurface, color: colors.warningText },
    bad: { backgroundColor: colors.dangerSurface, color: colors.danger },
    plain: { backgroundColor: colors.surfaceSunken, color: colors.black },
  }[tone];
  return (
    <View style={[styles.pill, { backgroundColor: look.backgroundColor }]}>
      <Txt size={12} weight="bold" color={look.color}>
        {label}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  table: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    overflow: "hidden",
  },
  row: { flexDirection: "row", alignItems: "center", paddingHorizontal: 12, paddingVertical: 12, minHeight: 44 },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  cell: { paddingHorizontal: 6 },
  tabular: { fontVariant: ["tabular-nums"] },
  pill: { alignSelf: "flex-start", borderRadius: 999, paddingVertical: 4, paddingHorizontal: 10 },
});
