import { View } from "react-native";
import { Redirect, Stack, useLocalSearchParams, useRouter } from "expo-router";

import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import {
  failureCopy,
  formatDateTime,
  formatMoney,
  formatTime,
  networkName,
  pendingApprovalsCode,
} from "@/payments/format";
import { DetailList, Loading, Timeline } from "@/payments/Parts";
import { Deposit } from "@/payments/types";
import { useDeposit } from "@/payments/useDeposit";
import { colors } from "@/theme";

/**
 * Where a payment stands. One address for the whole life of the payment, so a
 * client who closes the app and comes back sees the truth, whatever it is now.
 */
export default function DepositStatusScreen() {
  const router = useRouter();
  const { depositId } = useLocalSearchParams<{ depositId: string }>();
  const { deposit, missing, stale } = useDeposit(depositId);
  const close = () => router.replace("/");

  if (deposit?.status === "confirmed") {
    return <Redirect href={`/pay/receipt/${deposit.id}`} />;
  }

  if (missing) {
    return (
      <Screen signedIn footer={<Button label="Close" onPress={close} />}>
        <Stack.Screen options={{ title: "Payment" }} />
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">We could not find this payment.</Notice>
        </View>
      </Screen>
    );
  }

  if (!deposit) {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: "Payment" }} />
        {stale ? (
          <Notice tone="neutral">We could not reach Finvest. Trying again.</Notice>
        ) : (
          <Loading label="Loading your payment" />
        )}
      </Screen>
    );
  }

  if (deposit.status === "failed") {
    return (
      <Failed
        deposit={deposit}
        onRetry={() => router.replace(`/pay/${deposit.payable.id}`)}
        onClose={close}
      />
    );
  }

  const network = networkName[deposit.network];
  const payer = deposit.payer;
  const amount = formatMoney(deposit.amount);
  const checking = deposit.status === "checking";
  const sent = deposit.status !== "sending";

  return (
    <Screen signedIn footer={<TextLink label="Close" onPress={close} />}>
      <Stack.Screen options={{ title: checking ? "Checking your payment" : "Approve on your phone" }} />
      <View style={{ marginTop: 20, gap: 10 }} accessibilityLiveRegion="polite">
        <Title>{checking ? "We are checking your payment" : "Approve on your phone"}</Title>
        <Txt color={colors.muted}>
          {checking
            ? `${network} has not given us a clear answer about your ${amount} for ${deposit.payable.title}, so we are asking them what happened. This can take a few minutes.`
            : `${network} ${sent ? "has sent" : "is sending"} a prompt to ${payer}. Enter your mobile money PIN to pay ${amount} for your ${deposit.payable.title}.`}
        </Txt>
      </View>

      <View style={{ marginTop: 20, gap: 16 }}>
        {stale ? (
          <Notice tone="neutral">We could not refresh this just now. Trying again.</Notice>
        ) : null}
        {checking ? (
          <Notice tone="warning">
            {`Do not pay again. If the money left your phone, your investment starts as soon as ${network} confirms it. If it did not, nothing was taken.`}
          </Notice>
        ) : null}
        <Timeline
          steps={
            checking
              ? [
                  { label: "Payment requested", state: "done", note: formatTime(deposit.requestedAt) },
                  { label: `Asking ${network} what happened`, state: "now" },
                  { label: "Your investment starts", state: "later" },
                ]
              : [
                  {
                    label: `Request sent to ${network}`,
                    state: sent ? "done" : "now",
                    note: sent ? formatTime(deposit.requestedAt) : undefined,
                  },
                  { label: "Waiting for your PIN", state: sent ? "now" : "later" },
                  { label: "Your investment starts", state: "later" },
                ]
          }
        />
        {checking ? null : (
          <Txt size={13} color={colors.muted}>
            {`No prompt? Dial ${pendingApprovalsCode[deposit.network]} and look under pending approvals. You can close this screen. The payment carries on without it.`}
          </Txt>
        )}
      </View>
    </Screen>
  );
}

function Failed({
  deposit,
  onRetry,
  onClose,
}: {
  deposit: Deposit;
  onRetry: () => void;
  onClose: () => void;
}) {
  const network = networkName[deposit.network];
  const copy = failureCopy[deposit.failure ?? "other"];
  return (
    <Screen
      signedIn
      footer={
        <>
          <Button label="Try again" onPress={onRetry} testID="pay-retry" />
          <TextLink label="Close" onPress={onClose} />
        </>
      }
    >
      <Stack.Screen options={{ title: "Payment did not go through" }} />
      <View style={{ marginTop: 20, gap: 10 }} accessibilityLiveRegion="polite">
        <Title>Your payment did not go through</Title>
        <Txt color={colors.muted}>{copy.sentence(network, deposit.payer)}</Txt>
      </View>
      <View style={{ marginTop: 20, gap: 16 }}>
        <Card>
          <Txt>
            <Txt weight="bold">Nothing was taken</Txt> and no investment was started.
          </Txt>
        </Card>
        <DetailList
          rows={[
            { label: "You tried to pay", value: formatMoney(deposit.amount) },
            { label: "For", value: deposit.payable.title },
            { label: `${network} said`, value: copy.label },
            { label: "Time", value: formatDateTime(deposit.requestedAt) },
            { label: "Finvest reference", value: deposit.reference },
          ]}
        />
        <Txt size={13} color={colors.muted}>
          {`If money did leave your phone, do not pay again. Send us the ${network} message and we will trace it within one working day.`}
        </Txt>
      </View>
    </Screen>
  );
}
