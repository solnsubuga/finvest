import { useCallback, useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useBonds } from "@/bonds/BondsProvider";
import { QuoteFigures } from "@/bonds/Parts";
import { Bond, BondQuote, BondsError, bondPayableId, describeBondsError } from "@/bonds/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { IdCheckPrompt } from "@/identity/Parts";
import { Failed } from "@/investing/Parts";
import { useLoaded } from "@/investing/useLoaded";
import { newIdempotencyKey } from "@/payments/idempotency";
import { Loading } from "@/payments/Parts";
import { colors, fontSizes } from "@/theme";

/**
 * Review and confirm a bond purchase. Confirming saves it at today's price, then the client pays
 * for it. Nothing is paid on this screen.
 */
export default function BondReview() {
  const router = useRouter();
  const params = useLocalSearchParams<{ code: string; face: string }>();
  const client = useBonds();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Refused because the ID check is not approved yet: the error then opens it.
  const [needsId, setNeedsId] = useState(false);
  // One key per request: a double tap or a retry after a lost answer saves one purchase. After a
  // definite refusal nothing was saved, so the next try gets a new key.
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [unknown, setUnknown] = useState(false);

  const minor = Number(params.face);
  const [loaded, retry] = useLoaded(
    useCallback(async (): Promise<{ bond: Bond; quote: BondQuote }> => {
      if (!Number.isSafeInteger(minor) || minor <= 0) throw new BondsError("not_found");
      const bond = await client.getBond(params.code);
      return { bond, quote: await client.quote(bond.code, { currency: bond.currency, minor }) };
    }, [client, params.code, minor]),
    describeBondsError,
  );

  const confirm = async () => {
    if (loaded.state !== "ready") return;
    const { bond, quote } = loaded.value;
    setBusy(true);
    setError(null);
    try {
      const holding = await client.buy({ code: bond.code, face: quote.face, idempotencyKey });
      // Replace, so Back from paying does not land on a confirm button that buys again.
      router.replace(`/pay/${bondPayableId(holding.id)}`);
    } catch (caught) {
      setNeedsId(caught instanceof BondsError && caught.code === "not_verified");
      const lost = !(caught instanceof BondsError) || caught.code === "unavailable";
      setUnknown(lost);
      if (lost) {
        setError(`${describeBondsError(caught)} Trying again sends the same request, so you will not buy twice.`);
      } else {
        setIdempotencyKey(newIdempotencyKey());
        setError(describeBondsError(caught));
      }
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        loaded.state === "ready" ? (
          <Button label="Confirm and pay" variant="lime" onPress={confirm} loading={busy} testID="bond-confirm" />
        ) : null
      }
    >
      <Stack.Screen options={{ title: "Review your bond" }} />
      <BackButton fallback={`/invest/bond/${params.code}`} />
      <View style={{ marginTop: 20 }}>
        <Title>Review your bond</Title>
      </View>
      {loaded.state === "loading" ? (
        <Loading label="Loading your purchase" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 16 }}>
          <Txt size={fontSizes.lg} weight="bold">
            {loaded.value.bond.name}
          </Txt>
          <QuoteFigures calc={{ state: "ready", quote: loaded.value.quote }} />
          {error ? (
            needsId ? (
              <IdCheckPrompt message={error} />
            ) : (
              <Notice tone={unknown ? "warning" : "danger"}>{error}</Notice>
            )
          ) : null}
          <Txt size={fontSizes.xs} color={colors.muted}>
            {
              "Today's price is held for 24 hours while you pay by mobile money or card, because bond prices move with the market. You choose where coupons are paid from the bond in Portfolio."
            }
          </Txt>
        </View>
      )}
    </Screen>
  );
}
