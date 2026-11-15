import { useEffect } from "react";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { Screen } from "@/components/Screen";
import { Loading } from "@/payments/Parts";
import { usePayments } from "@/payments/PaymentsProvider";

/**
 * Where Pesapal sends the client back when they finish on its page. Coming back proves nothing:
 * it only asks for the payment to be read now, then shows the payment, which waits for
 * Pesapal's own record.
 */
export default function CardReturn() {
  const router = useRouter();
  const { payment } = useLocalSearchParams<{ payment?: string }>();
  const { client } = usePayments();

  useEffect(() => {
    if (!payment) {
      router.replace("/");
      return;
    }
    const go = () => router.replace(`/pay/card/${payment}?returned=1`);
    if (!client.cardReturned) {
      go();
      return;
    }
    client.cardReturned(payment).then(go, go);
  }, [client, payment, router]);

  return (
    <Screen signedIn>
      <Stack.Screen options={{ title: "Confirming your card payment" }} />
      <Loading label="Confirming your card payment" />
    </Screen>
  );
}
