import { useEffect } from "react";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { Screen } from "@/components/Screen";
import { Loading } from "@/payments/Parts";
import { usePayments } from "@/payments/PaymentsProvider";
import { ReturnedPayment } from "@/payments/types";

/**
 * Where Pesapal sends the client back when they finish on its page, after paying by card or by
 * mobile money there. Coming back proves nothing:
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
    // A card payment or mobile money paid on the same page: each has its own screen. If the
    // payment cannot be read just now, the card screen says so and keeps trying.
    const go = (kind: ReturnedPayment = "card") =>
      router.replace(`/pay/${kind === "card" ? "card" : "deposit"}/${payment}?returned=1`);
    if (!client.cardReturned) {
      go();
      return;
    }
    client.cardReturned(payment).then(go, () => go());
  }, [client, payment, router]);

  return (
    <Screen signedIn>
      <Stack.Screen options={{ title: "Confirming your payment" }} />
      <Loading label="Confirming your payment" />
    </Screen>
  );
}
