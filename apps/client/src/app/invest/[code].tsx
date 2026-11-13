import { useCallback, useEffect, useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Field, Input } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatAmount, formatDay, groupDigits, kindLabel, parseWholeAmount, termLabel } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { describeInvestingError, Product, Quote } from "@/investing/types";
import { useLoaded } from "@/investing/useLoaded";
import { DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes } from "@/theme";

/** How long typing must pause before the quote is asked for. */
const QUOTE_DELAY_MS = 400;

type Calc = { state: "idle" } | { state: "working" } | { state: "failed"; message: string } | { state: "ready"; quote: Quote };

/**
 * A product, with a calculator: type an amount and see what it earns after tax
 * and when it matures. The figures are the backend's quote. The app works out none of them.
 */
export default function ProductDetail() {
  const router = useRouter();
  const { code } = useLocalSearchParams<{ code: string }>();
  const client = useInvesting();
  const [products, retry] = useLoaded(useCallback(() => client.listProducts(), [client]));
  const product = products.state === "ready" ? products.value.find((p) => p.code === code) : undefined;

  const [text, setText] = useState("");
  // The last answer, kept with the request it answers, so a stale quote is never shown.
  const [answer, setAnswer] = useState<{ key: string; calc: Calc } | null>(null);
  const amount = product ? parseWholeAmount(text, product.currency) : null;
  const key = product && amount ? `${product.code}:${amount.currency}:${amount.minor}` : null;
  const calc: Calc = !key ? { state: "idle" } : answer?.key === key ? answer.calc : { state: "working" };

  useEffect(() => {
    if (!key || !product || !amount) {
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      client.quote(product.code, amount).then(
        (quote) => live && setAnswer({ key, calc: { state: "ready", quote } }),
        (error: unknown) => live && setAnswer({ key, calc: { state: "failed", message: describeInvestingError(error) } }),
      );
    }, QUOTE_DELAY_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // The key holds the product and the amount by value, so the same figure is not quoted twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, key]);

  const ready = calc.state === "ready" && product?.status === "live";

  return (
    <Screen
      signedIn
      footer={
        product ? (
          <Button
            label="Review"
            disabled={!ready}
            onPress={() => router.push({ pathname: "/invest/review", params: { code: product.code, amount: String(amount?.minor ?? "") } })}
            testID="review"
          />
        ) : null
      }
    >
      <Stack.Screen options={{ title: product?.name ?? "Invest" }} />
      <BackButton fallback="/invest" />
      {products.state === "loading" ? (
        <Loading label="Loading the product" />
      ) : products.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={products.message} onRetry={retry} />
        </View>
      ) : !product ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">We could not find that product.</Notice>
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 18 }}>
          <View style={{ gap: 6 }}>
            <Eyebrow>{`${kindLabel[product.kind]} · ${termLabel(product)}`}</Eyebrow>
            <Title>{product.name}</Title>
          </View>
          <Headline product={product} />
          {product.status === "paused" ? (
            <Notice tone="warning">This product is paused for new investments. You can still see the rate.</Notice>
          ) : null}
          <Field
            label="How much do you want to invest?"
            hint={`From ${formatAmount(product.minimum)}`}
            error={calc.state === "failed" ? calc.message : text && !amount ? "Type a whole amount, such as 1,000,000." : null}
          >
            <Input
              label="Amount"
              value={text}
              onChangeText={(typed) => setText(groupDigits(typed))}
              keyboardType="number-pad"
              placeholder={groupDigits(String(product.minimum.minor / (product.currency === "USD" ? 100 : 1)))}
              invalid={calc.state === "failed"}
              testID="amount"
            />
          </Field>
          <Calculation calc={calc} product={product} />
        </View>
      )}
    </Screen>
  );
}

function Headline({ product }: { product: Product }) {
  return (
    <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 }}>
      <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
        Yearly rate after tax
      </Txt>
      <Txt size={40} weight="extrabold" lineHeight={46} color={colors.lime} style={{ letterSpacing: -1 }}>
        {`${product.ratePercent}%`}
      </Txt>
      <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
        {product.termDays === null
          ? `Interest paid monthly. Leave it in as long as you like.`
          : `Fixed for ${product.termDays} days. Interest is added daily and paid at maturity.`}
      </Txt>
    </View>
  );
}

function Calculation({ calc, product }: { calc: Calc; product: Product }) {
  if (calc.state === "idle") {
    return (
      <Txt size={fontSizes.sm} color={colors.muted}>
        Type an amount to see what it would earn.
      </Txt>
    );
  }
  if (calc.state === "working") {
    return <Loading label="Working out what it would earn" />;
  }
  if (calc.state === "failed") {
    return null;
  }
  const { quote } = calc;
  return (
    <View style={{ gap: 8 }} testID="quote">
      <DetailList
        rows={[
          { label: "You invest", value: formatAmount(quote.amount) },
          ...(quote.interest && quote.total && quote.maturesOn
            ? [
                { label: "Interest after tax", value: formatAmount(quote.interest) },
                { label: "You get back", value: formatAmount(quote.total) },
                { label: "Matures on", value: formatDay(quote.maturesOn) },
              ]
            : [{ label: "Interest", value: `${product.ratePercent}% a year after tax, paid monthly` }]),
        ]}
      />
      <Txt size={fontSizes.xs} color={colors.muted}>
        If you paid today. The term starts on the day your payment is confirmed.
      </Txt>
    </View>
  );
}
