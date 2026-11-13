import { useRouter } from "expo-router";

import { AppPage } from "@/components/AppPage";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Txt } from "@/components/Txt";
import { usePayments } from "@/payments/PaymentsProvider";
import { colors, fontSizes } from "@/theme";

export default function Invest() {
  const router = useRouter();
  const { demoPayableId } = usePayments();
  return (
    <AppPage title="Invest">
      <Notice tone="neutral">
        Nothing here yet. Treasury bills and fixed income products you can invest in will be listed
        here.
      </Notice>
      <Txt size={fontSizes.sm} color={colors.muted}>
        This page is part of the app shell. Its content arrives in a later phase.
      </Txt>
      {demoPayableId ? (
        // Only in the demo, until the investing screens lead here themselves.
        <Button
          label="Demo: pay for an investment"
          onPress={() => router.push(`/pay/${demoPayableId}`)}
          testID="demo-pay"
        />
      ) : null}
    </AppPage>
  );
}
