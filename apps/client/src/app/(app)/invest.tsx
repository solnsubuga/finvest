import { useCallback } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";

import { AppPage } from "@/components/AppPage";
import { Button } from "@/components/Button";
import { Txt } from "@/components/Txt";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed, ProductCard } from "@/investing/Parts";
import { useLoaded } from "@/investing/useLoaded";
import { Loading } from "@/payments/Parts";
import { usePayments } from "@/payments/PaymentsProvider";
import { colors, fontSizes } from "@/theme";

/** What a client can invest in, with the rate after tax. */
export default function Invest() {
  const router = useRouter();
  const client = useInvesting();
  const { demoPayableId } = usePayments();
  const [products, retry] = useLoaded(useCallback(() => client.listProducts(), [client]));

  return (
    <AppPage title="Invest" subtitle="Government-backed and fixed income products. Rates shown are after tax.">
      {products.state === "loading" ? (
        <Loading label="Loading products" />
      ) : products.state === "failed" ? (
        <Failed message={products.message} onRetry={retry} />
      ) : (
        <View style={{ gap: 12 }}>
          {products.value.map((product) => (
            <ProductCard
              key={product.code}
              product={product}
              onPress={() => router.push(`/invest/${product.code}`)}
            />
          ))}
          <Txt size={fontSizes.xs} color={colors.muted}>
            Rates can change for new investments. Once you invest, your rate is fixed for the whole term.
          </Txt>
        </View>
      )}
      {demoPayableId ? (
        // Only in the demo: the payment screens on their own, without making an investment first.
        <Button
          label="Demo: pay for an investment"
          onPress={() => router.push(`/pay/${demoPayableId}`)}
          testID="demo-pay"
        />
      ) : null}
    </AppPage>
  );
}
