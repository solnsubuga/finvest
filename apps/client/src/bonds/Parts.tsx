import { useCallback } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { formatAmount, formatDay } from "@/investing/format";
import { useLoaded } from "@/investing/useLoaded";
import { DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes, radii } from "@/theme";

import { useBonds } from "./BondsProvider";
import { formatPrice, holdingLine, holdingStatusLabel, maturesLabel, notBuyableLabel, priceLabel } from "./format";
import { Bond, BondHolding, describeBondsError } from "./types";
import { BondCalc } from "./useBondQuote";

/** One issue in the list: its name, the coupon after tax, when it matures and today's price. */
export function BondCard({ bond, onPress }: { bond: Bond; onPress: () => void }) {
  const closed = notBuyableLabel(bond);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${bond.name}, ${bond.couponPercent}% a year after tax, ${maturesLabel(bond).toLowerCase()}${closed ? `, ${closed.toLowerCase()}` : ""}`}
      onPress={onPress}
      testID={`bond-${bond.code}`}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.85 }]}
    >
      <View style={{ flex: 1, gap: 4 }}>
        <Txt size={fontSizes.xs} weight="bold" color={colors.muted}>
          {`Treasury bond · ${maturesLabel(bond)}`}
        </Txt>
        <Txt size={fontSizes.lg} weight="bold">
          {bond.name}
        </Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {closed ?? `From ${formatAmount(bond.minimumFace)} · price ${priceLabel(bond.price)}`}
        </Txt>
        {closed ? null : (
          <Txt size={fontSizes.xs} color={colors.muted}>
            Coupon paid every six months
          </Txt>
        )}
      </View>
      <View style={{ alignItems: "flex-end", gap: 2 }}>
        <Txt size={24} weight="extrabold" lineHeight={30} style={{ letterSpacing: -0.5 }}>
          {`${bond.couponPercent}%`}
        </Txt>
        <Txt size={fontSizes.xs} weight="semibold" color={colors.muted}>
          coupon after tax
        </Txt>
      </View>
      <Icon name="chevron" size={18} color={colors.muted} />
    </Pressable>
  );
}

/**
 * The bonds on Invest, under the bills. They load on their own, so a bond list that fails
 * leaves the bills usable, and an empty one shows nothing at all.
 */
export function BondsSection({ columns = 1 }: { columns?: number }) {
  const router = useRouter();
  const client = useBonds();
  const [bonds, retry] = useLoaded(
    useCallback(() => client.listBonds(), [client]),
    describeBondsError,
  );
  if (bonds.state === "ready" && bonds.value.length === 0) return null;
  return (
    <View style={{ gap: 12 }} testID="bonds">
      <View style={{ gap: 2 }}>
        <Txt size={fontSizes.lg} weight="extrabold" accessibilityRole="header">
          Treasury bonds
        </Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          Buy part of a Bank of Uganda bond. It pays a coupon every six months, and the face value when it matures.
        </Txt>
      </View>
      {bonds.state === "loading" ? (
        <Loading label="Loading bonds" />
      ) : bonds.state === "failed" ? (
        <View style={{ gap: 12 }}>
          <Notice tone="danger">{bonds.message}</Notice>
          <Button label="Try again" onPress={retry} height={48} testID="bonds-retry" />
        </View>
      ) : (
        <View style={columns > 1 ? styles.grid : { gap: 12 }}>
          {bonds.value.map((bond) => (
            <View key={bond.code} style={columns > 1 ? { flexBasis: `${100 / columns - 2}%`, flexGrow: 1 } : undefined}>
              <BondCard bond={bond} onPress={() => router.push(`/invest/bond/${bond.code}`)} />
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

/** One of the client's bond holdings in Portfolio. */
export function HoldingCard({ holding, onPress }: { holding: BondHolding; onPress: () => void }) {
  const waiting = holding.status === "unpaid";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${holding.name}, ${holdingStatusLabel[holding.status]}, face value ${formatAmount(holding.face)}, ${holdingLine(holding)}`}
      onPress={onPress}
      testID={`holding-${holding.id}`}
      style={({ pressed }) => [styles.card, { alignItems: "flex-start" }, pressed && { opacity: 0.85 }]}
    >
      <View style={{ flex: 1, gap: 4 }}>
        <Txt size={fontSizes.xs} weight="bold" color={waiting ? colors.warningText : colors.muted}>
          {holdingStatusLabel[holding.status]}
        </Txt>
        <Txt size={fontSizes.lg} weight="bold">
          {holding.name}
        </Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {waiting ? `Pay ${formatAmount(holding.cost)} to buy it` : holdingLine(holding)}
        </Txt>
      </View>
      <View style={{ alignItems: "flex-end", gap: 2 }}>
        <Txt size={fontSizes.lg} weight="extrabold">
          {formatAmount(holding.face)}
        </Txt>
        <Txt size={fontSizes.xs} weight="semibold" color={colors.muted}>
          face value
        </Txt>
      </View>
    </Pressable>
  );
}

/** A quote's figures, as the detail and the review show them. */
export function QuoteFigures({ calc }: { calc: Extract<BondCalc, { state: "ready" }> }) {
  const { quote } = calc;
  return (
    <View style={{ gap: 8 }} testID="bond-quote">
      <DetailList
        rows={[
          { label: "Face value", value: formatAmount(quote.face) },
          { label: "Price", value: `${formatPrice(quote.price)} per 100` },
          { label: "Interest since the last coupon", value: formatAmount(quote.accrued) },
          { label: "You pay", value: formatAmount(quote.cost) },
          { label: "Each coupon after tax", value: formatAmount(quote.coupon) },
          { label: "Coupons left", value: `${quote.couponsLeft}, the next on ${formatDay(quote.nextCouponDate)}` },
          { label: "Back by maturity", value: formatAmount(quote.totalBack) },
          { label: "Gain by maturity", value: formatAmount(quote.gain) },
        ]}
      />
      <Txt size={fontSizes.xs} color={colors.muted}>
        If you paid today. You pay the interest earned since the last coupon on top of the price, and the next coupon pays it back in full.
      </Txt>
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
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
});
