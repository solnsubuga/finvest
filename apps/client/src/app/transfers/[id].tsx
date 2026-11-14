import { useCallback, useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { canCancel, failureReason, transferAmount, transferStatusLabel, transferTitle } from "@/investing/transfers";
import { describeInvestingError, Transfer } from "@/investing/types";
import { useLoaded } from "@/investing/useLoaded";
import { formatDateTime } from "@/payments/format";
import { Detail, DetailList, Loading } from "@/payments/Parts";
import { colors, fontSizes } from "@/theme";

/** One transfer: who it went to or came from, what it is now, and the new investment. */
export default function TransferDetail() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const client = useInvesting();
  const [loaded, retry] = useLoaded(useCallback(() => client.getTransfer(id), [client, id]));
  const [cancelled, setCancelled] = useState<Transfer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const transfer = cancelled ?? (loaded.state === "ready" ? loaded.value : null);
  const cancel = async () => {
    setBusy(true);
    setError(null);
    try {
      setCancelled(await client.cancelTransfer(id));
    } catch (caught) {
      setError(describeInvestingError(caught));
    }
    setBusy(false);
  };

  return (
    <Screen
      signedIn
      footer={
        transfer && canCancel(transfer) ? (
          <Button label="Cancel this transfer" variant="secondary" onPress={cancel} loading={busy} testID="cancel-transfer" />
        ) : null
      }
    >
      <Stack.Screen options={{ title: transfer ? transferTitle(transfer) : "Transfer" }} />
      <BackButton fallback="/transfers" />
      {loaded.state === "loading" ? (
        <Loading label="Loading the transfer" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <Failed message={loaded.message} onRetry={retry} />
        </View>
      ) : transfer ? (
        <Body transfer={transfer} error={error} onOpen={(investmentId) => router.push(`/investment/${investmentId}`)} />
      ) : null}
    </Screen>
  );
}

function Body({ transfer, error, onOpen }: { transfer: Transfer; error: string | null; onOpen: (investmentId: string) => void }) {
  const sent = transfer.direction === "sent";
  const first = transfer.otherName.split(" ")[0];
  const rows: Detail[] = [
    { label: sent ? "To" : "From", value: transfer.otherName },
    { label: "Asked for", value: formatDateTime(transfer.requestedAt) },
  ];
  if (transfer.finishedAt && transfer.status === "done") {
    rows.push({ label: sent ? "Sent" : "Received", value: formatDateTime(transfer.finishedAt) });
  }
  if (transfer.message) {
    rows.push({ label: "Message", value: transfer.message });
  }
  return (
    <View style={{ marginTop: 20, gap: 16 }}>
      <View style={{ gap: 6 }}>
        <Eyebrow>{transferStatusLabel(transfer)}</Eyebrow>
        <Title>{transferTitle(transfer)}</Title>
      </View>
      <View style={{ backgroundColor: colors.black, borderRadius: 22, padding: 20, gap: 4 }}>
        <Txt size={fontSizes.sm} weight="semibold" color={colors.onBlackMuted}>
          {sent ? "You sent" : "You received"}
        </Txt>
        <Txt size={32} weight="extrabold" lineHeight={38} color={colors.white} style={{ letterSpacing: -0.6 }} testID="transfer-amount">
          {transferAmount(transfer)}
        </Txt>
        <Txt size={fontSizes.sm} weight="semibold" color={colors.lime}>
          Same rate and maturity date. Nothing was cashed out.
        </Txt>
      </View>
      <DetailList rows={rows} />
      {transfer.status === "held" || transfer.status === "release_proposed" ? (
        <Notice tone="warning">
          {`Finvest checks large transfers before they go. It is usually done within a working day, and we will tell you when ${first} has it. You can cancel until then.`}
        </Notice>
      ) : transfer.status === "waiting" ? (
        <Notice tone="neutral">{`On its way to ${first}. It usually takes a minute.`}</Notice>
      ) : transfer.status === "failed" ? (
        <Notice tone="danger">{failureReason(transfer.failureCode)}</Notice>
      ) : transfer.status === "declined" ? (
        <Notice tone="danger">Finvest could not let this transfer through. Your investment is as it was. Contact support to find out more.</Notice>
      ) : transfer.status === "cancelled" ? (
        <Notice tone="neutral">You cancelled this. Your investment is as it was.</Notice>
      ) : sent ? (
        <Notice tone="neutral">
          {transfer.keptInvestmentId
            ? `${first} has a new investment on your rate. What you kept is in a new investment of your own, still earning.`
            : `${first} has a new investment on your rate. You sent all of it.`}
        </Notice>
      ) : (
        <Notice tone="neutral">It is a new investment in your name, earning the same rate until it matures. It rolls over unless you change it.</Notice>
      )}
      {error ? <Notice tone="danger">{error}</Notice> : null}
      {transfer.status === "done" && !sent && transfer.investmentId ? (
        <TextLink label="Open your new investment" onPress={() => onOpen(transfer.investmentId as string)} testID="open-received" />
      ) : null}
      {transfer.status === "done" && sent && transfer.keptInvestmentId ? (
        <TextLink label="Open what you kept" onPress={() => onOpen(transfer.keptInvestmentId as string)} testID="open-kept" />
      ) : null}
    </View>
  );
}
