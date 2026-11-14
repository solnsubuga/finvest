import { useCallback, useState } from "react";
import { Pressable, StyleSheet, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import { Button } from "@/components/Button";
import { DataTable, Pill } from "@/components/DataTable";
import { Txt } from "@/components/Txt";
import { colors, fontFor } from "@/theme";

import { formatAmount, formatDay, groupDigits, kindLabel, parseWholeAmount, termLabel, wholeUnits } from "./format";
import { useInvesting } from "./InvestingProvider";
import { Product, Quote } from "./types";
import { useLoaded } from "./useLoaded";
import { useQuote } from "./useQuote";

/** The amount the compare table quotes, in shillings, as the canvas has it. */
const COMPARE_MINOR = 1_000_000;

/** The amount the calculator starts with: UGX 1,000,000, or the product's minimum when that is more. */
function startingText(product: Product): string {
  const start = product.currency === "UGX" ? Math.max(COMPARE_MINOR, product.minimum.minor) : product.minimum.minor;
  return wholeUnits({ currency: product.currency, minor: start });
}

/**
 * Invest on a wide screen: the products and a table comparing them on the left,
 * a calculator for the chosen one on the right. Every figure is the backend's
 * quote after tax. The app works out none of them.
 */
export function WideInvest({ products }: { products: Product[] }) {
  const router = useRouter();
  const client = useInvesting();
  const firstLive = products.find((product) => product.status === "live") ?? products[0];
  const [code, setCode] = useState(firstLive?.code ?? null);
  const product = products.find((item) => item.code === code) ?? firstLive;
  const [text, setText] = useState(product ? startingText(product) : "");
  const amount = product ? parseWholeAmount(text, product.currency) : null;
  const calc = useQuote(client, product, amount);

  // One quote per shilling product with a term, for the same amount, so the table compares like with like.
  const [compare] = useLoaded(
    useCallback(async () => {
      const entries = await Promise.all(
        products.map(async (item): Promise<[string, Quote | null]> => {
          if (item.currency !== "UGX" || item.termDays === null) return [item.code, null];
          try {
            return [item.code, await client.quote(item.code, { currency: "UGX", minor: COMPARE_MINOR })];
          } catch {
            return [item.code, null];
          }
        }),
      );
      return new Map(entries);
    }, [client, products]),
  );
  const quotes = compare.state === "ready" ? compare.value : new Map<string, Quote | null>();
  const waiting = compare.state === "loading";

  const choose = (next: Product) => {
    if (next.currency !== product?.currency) setText(startingText(next));
    setCode(next.code);
  };

  const columns = [{ key: "label", label: "Compare", flex: 1.4 }, ...products.map((item) => ({ key: item.code, label: item.name }))];
  const line = (key: string, label: string, value: (item: Product) => string) => ({
    key,
    cells: Object.fromEntries([["label", label], ...products.map((item) => [item.code, value(item)])]),
  });
  const rows = [
    line("earns", `${formatAmount({ currency: "UGX", minor: COMPARE_MINOR })} earns`, (item) => {
      if (item.termDays === null) return item.currency === "UGX" ? "Paid monthly" : "n/a";
      const quote = quotes.get(item.code);
      return quote?.interest ? wholeUnits(quote.interest) : waiting ? "…" : "n/a";
    }),
    line("back", "You get it back on", (item) => {
      if (item.termDays === null) return "When you ask";
      const quote = quotes.get(item.code);
      return quote?.maturesOn ? formatDay(quote.maturesOn) : waiting ? "…" : "n/a";
    }),
    line("early", "Need it early?", () => "On request"),
    line("interest", "Interest", (item) => (item.interestPaid === "monthly" ? "Daily, paid monthly" : "Daily, paid at maturity")),
  ];

  const ready = calc.state === "ready" && product?.status === "live";
  const quote = calc.state === "ready" ? calc.quote : null;
  // "…" while the quote is on its way; a dash when there is no amount to quote.
  const pending = calc.state === "working" ? "…" : "–";

  return (
    <View style={styles.columns}>
      <View style={{ flex: 3, minWidth: 480, gap: 16 }}>
        <View style={styles.grid}>
          {products.map((item) => (
            <ProductTile key={item.code} product={item} selected={item.code === product?.code} onPress={() => choose(item)} />
          ))}
        </View>
        <DataTable columns={columns} rows={rows} testID="compare" />
        <Txt size={13} color={colors.muted}>
          Returns are shown after tax. Rates are today&apos;s and are fixed for your investment once it starts.
        </Txt>
      </View>
      {product ? (
        <View style={{ flex: 2, minWidth: 320 }}>
          <View style={styles.black} testID="calculator">
            <Txt size={17} weight="extrabold" color={colors.white} accessibilityRole="header">
              {product.name}
            </Txt>
            <View style={{ gap: 4 }}>
              <Txt size={13} color={colors.onBlackMuted}>
                If you invest
              </Txt>
              <View style={styles.amountBox}>
                <Txt size={15} weight="bold" color={colors.onBlackMuted}>
                  {product.currency}
                </Txt>
                <TextInput
                  accessibilityLabel="Amount to invest"
                  value={text}
                  onChangeText={(typed) => setText(groupDigits(typed))}
                  keyboardType="number-pad"
                  placeholder={wholeUnits(product.minimum)}
                  placeholderTextColor={colors.onBlackMuted}
                  style={styles.amountInput}
                  testID="calc-amount"
                />
              </View>
            </View>
            <Txt size={12} color={calc.state === "failed" ? colors.lime : colors.onBlackMuted} accessibilityLiveRegion="polite">
              {calc.state === "failed"
                ? calc.message
                : text && !amount
                  ? "Type a whole amount, such as 1,000,000."
                  : `From ${formatAmount(product.minimum)}. ${product.status === "paused" ? "Paused for new investments." : ""}`.trim()}
            </Txt>
            <View style={styles.pair}>
              <View style={styles.inner}>
                <Txt size={12} color={colors.onBlackMuted}>
                  You earn
                </Txt>
                <Txt size={18} weight="extrabold" color={colors.lime} testID="calc-earn">
                  {quote?.interest ? formatAmount(quote.interest) : quote ? `${product.ratePercent}% a year` : pending}
                </Txt>
              </View>
              <View style={styles.inner}>
                <Txt size={12} color={colors.onBlackMuted}>
                  You get back
                </Txt>
                <Txt size={18} weight="extrabold" color={colors.white} testID="calc-back">
                  {quote?.total ? formatAmount(quote.total) : quote ? "When you ask" : pending}
                </Txt>
              </View>
            </View>
            <DarkLine label="Starts" value="When your payment is confirmed" />
            <DarkLine label="Matures" value={quote?.maturesOn ? formatDay(quote.maturesOn) : product.termDays === null ? "Open term" : pending} />
            {product.termDays !== null ? <DarkLine label="At maturity" value="Roll over, you can change this" /> : null}
            <View style={{ marginTop: 6 }}>
              <Button
                label="Continue"
                variant="lime"
                height={48}
                disabled={!ready}
                onPress={() =>
                  router.push({ pathname: "/invest/review", params: { code: product.code, amount: String(amount?.minor ?? "") } })
                }
                testID="calc-continue"
              />
            </View>
          </View>
        </View>
      ) : null}
    </View>
  );
}

function ProductTile({ product, selected, onPress }: { product: Product; selected: boolean; onPress: () => void }) {
  const paused = product.status === "paused";
  return (
    <Pressable
      accessibilityRole="button"
      aria-selected={selected}
      accessibilityLabel={`${product.name}, ${product.ratePercent}% a year after tax${paused ? ", paused" : ""}`}
      onPress={onPress}
      style={({ pressed }) => [styles.tile, selected && styles.tileSelected, pressed && { opacity: 0.85 }]}
      testID={`product-${product.code}`}
    >
      <View style={[styles.between, { flexWrap: "wrap" }]}>
        <Pill label={kindLabel[product.kind]} tone="plain" />
        {selected ? <Pill label="Selected" tone="good" /> : paused ? <Pill label="Paused" tone="waiting" /> : null}
      </View>
      <Txt size={17} weight="extrabold">
        {product.name}
      </Txt>
      <View style={{ flexDirection: "row", alignItems: "baseline", gap: 6 }}>
        <Txt size={30} weight="extrabold" lineHeight={36} style={{ letterSpacing: -0.8 }}>
          {`${product.ratePercent}%`}
        </Txt>
        <Txt size={13} color={colors.muted}>
          a year, after tax
        </Txt>
      </View>
      <Txt size={13} color={colors.muted}>
        {`${termLabel(product)}. From ${formatAmount(product.minimum)}.`}
      </Txt>
    </Pressable>
  );
}

function DarkLine({ label, value }: { label: string; value: string }) {
  return (
    <View style={[styles.between, styles.darkTop]}>
      <Txt size={14} color={colors.onBlackMuted}>
        {label}
      </Txt>
      <Txt size={14} weight="bold" color={colors.white} style={{ textAlign: "right", flexShrink: 1 }}>
        {value}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  columns: { flexDirection: "row", flexWrap: "wrap", gap: 20, alignItems: "flex-start" },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 16 },
  tile: {
    flexGrow: 1,
    flexBasis: 230,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 18,
    gap: 8,
  },
  tileSelected: { borderWidth: 2, borderColor: colors.black, padding: 17 },
  between: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 12 },
  black: { backgroundColor: colors.black, borderRadius: 20, padding: 20, gap: 10 },
  amountBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderBottomWidth: 1,
    borderBottomColor: colors.onBlackBorder,
    minHeight: 44,
  },
  amountInput: {
    color: colors.white,
    fontFamily: fontFor("extrabold"),
    fontSize: 24,
    flex: 1,
    minWidth: 0,
    paddingVertical: 4,
  },
  pair: { flexDirection: "row", gap: 12 },
  inner: { flex: 1, backgroundColor: colors.onBlackSurface, borderRadius: 16, padding: 12, gap: 2 },
  darkTop: { borderTopWidth: 1, borderTopColor: colors.onBlackDivider, paddingTop: 10 },
});
