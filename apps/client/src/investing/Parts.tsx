import { Pressable, StyleSheet, View } from "react-native";

import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors, fontSizes, radii } from "@/theme";

import { formatAmount, formatDay, kindLabel, statusLabel, termLabel } from "./format";
import { Investment, Product } from "./types";

/** One product in the list: its name, the rate after tax and the terms. */
export function ProductCard({ product, onPress }: { product: Product; onPress: () => void }) {
  const paused = product.status === "paused";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${product.name}, ${product.ratePercent}% a year after tax${paused ? ", paused" : ""}`}
      onPress={onPress}
      testID={`product-${product.code}`}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.85 }]}
    >
      <View style={{ flex: 1, gap: 4 }}>
        <Txt size={fontSizes.xs} weight="bold" color={colors.muted}>
          {`${kindLabel[product.kind]} · ${termLabel(product)}`}
        </Txt>
        <Txt size={fontSizes.lg} weight="bold">
          {product.name}
        </Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {paused ? "Paused for new investments" : `From ${formatAmount(product.minimum)}`}
        </Txt>
      </View>
      <View style={{ alignItems: "flex-end", gap: 2 }}>
        <Txt size={24} weight="extrabold" lineHeight={30} style={{ letterSpacing: -0.5 }}>
          {`${product.ratePercent}%`}
        </Txt>
        <Txt size={fontSizes.xs} weight="semibold" color={colors.muted}>
          a year after tax
        </Txt>
      </View>
      <Icon name="chevron" size={18} color={colors.muted} />
    </Pressable>
  );
}

/** A load that failed, with a way to try again. */
export function Failed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <View style={{ gap: 12 }}>
      <Notice tone="danger">{message}</Notice>
      <Button label="Try again" onPress={onRetry} height={48} testID="retry" />
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.xl,
    padding: 18,
  },
});

/** One of the client's investments in the portfolio. */
export function InvestmentCard({ investment, onPress }: { investment: Investment; onPress: () => void }) {
  const waiting = investment.status === "awaiting_payment";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${investment.productName}, ${statusLabel[investment.status]}, worth ${formatAmount(investment.value)}`}
      onPress={onPress}
      testID={`investment-${investment.id}`}
      style={({ pressed }) => [styles.card, { alignItems: "flex-start" }, pressed && { opacity: 0.85 }]}
    >
      <View style={{ flex: 1, gap: 4 }}>
        <Txt size={fontSizes.xs} weight="bold" color={waiting ? colors.warningText : colors.muted}>
          {statusLabel[investment.status]}
        </Txt>
        <Txt size={fontSizes.lg} weight="bold">
          {investment.productName}
        </Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {waiting
            ? `Pay ${formatAmount(investment.amount)} to start`
            : investment.maturityDate
              ? `${investment.status === "matured" ? "Matured" : "Matures"} ${formatDay(investment.maturityDate)}`
              : `${investment.ratePercent}% a year after tax`}
        </Txt>
      </View>
      <View style={{ alignItems: "flex-end", gap: 2 }}>
        <Txt size={fontSizes.lg} weight="extrabold">
          {formatAmount(investment.value)}
        </Txt>
        {investment.interestToday.minor > 0 ? (
          <Txt size={fontSizes.xs} weight="bold" color={colors.success}>
            {`+${formatAmount(investment.interestToday)} today`}
          </Txt>
        ) : investment.interestSoFar.minor > 0 ? (
          <Txt size={fontSizes.xs} weight="semibold" color={colors.muted}>
            {`+${formatAmount(investment.interestSoFar)} so far`}
          </Txt>
        ) : null}
      </View>
    </Pressable>
  );
}
