import { useCallback } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";

import { AppPage } from "@/components/AppPage";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { LinkCard } from "@/components/LinkCard";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed, InvestmentCard } from "@/investing/Parts";
import { useLoaded } from "@/investing/useLoaded";
import { Loading } from "@/payments/Parts";

/** The client's investments, newest first, with what each is worth after tax. */
export default function Portfolio() {
  const router = useRouter();
  const client = useInvesting();
  const [investments, retry] = useLoaded(useCallback(() => client.listInvestments(), [client]));

  return (
    <AppPage title="Portfolio" subtitle="What your investments are worth today, after tax.">
      {investments.state === "loading" ? (
        <Loading label="Loading your investments" />
      ) : investments.state === "failed" ? (
        <Failed message={investments.message} onRetry={retry} />
      ) : investments.value.length === 0 ? (
        <View style={{ gap: 12 }}>
          <Notice tone="neutral">You have no investments yet. Pick a product to make your first one.</Notice>
          <Button label="See what you can invest in" onPress={() => router.push("/invest")} testID="go-invest" />
        </View>
      ) : (
        <View style={{ gap: 12 }}>
          {investments.value.map((investment) => (
            <InvestmentCard
              key={investment.id}
              investment={investment}
              onPress={() => router.push(`/investment/${investment.id}`)}
            />
          ))}
          <LinkCard
            icon="send"
            title="Sent and received"
            description="Investments you sent to other clients, and those sent to you"
            onPress={() => router.push("/transfers")}
            testID="open-transfers"
          />
        </View>
      )}
    </AppPage>
  );
}
