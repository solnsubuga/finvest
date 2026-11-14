import { useCallback } from "react";
import { Pressable, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { formatDay } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { Failed } from "@/investing/Parts";
import { transferAmount, transferStatusLabel, transferTitle } from "@/investing/transfers";
import { Transfer } from "@/investing/types";
import { useLoaded } from "@/investing/useLoaded";
import { Loading } from "@/payments/Parts";
import { colors, fontSizes, radii } from "@/theme";

/** Investments the client sent to other clients, and those they received, newest first. */
export default function Transfers() {
  const router = useRouter();
  const client = useInvesting();
  const [loaded, retry] = useLoaded(useCallback(() => client.listTransfers(), [client]));

  return (
    <Screen signedIn>
      <Stack.Screen options={{ title: "Sent and received" }} />
      <BackButton fallback="/portfolio" />
      <View style={{ marginTop: 20, gap: 6 }}>
        <Title>Sent and received</Title>
        <Txt color={colors.muted}>Investments you sent to other Finvest clients, and those sent to you.</Txt>
      </View>
      <View style={{ marginTop: 20 }}>
        {loaded.state === "loading" ? (
          <Loading label="Loading what you sent and received" />
        ) : loaded.state === "failed" ? (
          <Failed message={loaded.message} onRetry={retry} />
        ) : loaded.value.length === 0 ? (
          <Notice tone="neutral">
            Nothing yet. Open a running investment and choose Send to someone to give part of it to another Finvest client.
          </Notice>
        ) : (
          <View style={{ gap: 10 }}>
            {loaded.value.map((transfer) => (
              <TransferRow key={transfer.id} transfer={transfer} onPress={() => router.push(`/transfers/${transfer.id}`)} />
            ))}
          </View>
        )}
      </View>
    </Screen>
  );
}

function TransferRow({ transfer, onPress }: { transfer: Transfer; onPress: () => void }) {
  const sent = transfer.direction === "sent";
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={transferTitle(transfer)}
      onPress={onPress}
      testID={`transfer-${transfer.id}`}
      style={({ pressed }) => ({
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
        padding: 14,
        borderRadius: radii.lg,
        backgroundColor: colors.white,
        borderWidth: 1,
        borderColor: colors.border,
        opacity: pressed ? 0.7 : 1,
      })}
    >
      <View
        style={{
          width: 40,
          height: 40,
          borderRadius: 20,
          backgroundColor: sent ? colors.black : colors.lime,
          alignItems: "center",
          justifyContent: "center",
          transform: [{ rotate: sent ? "-45deg" : "135deg" }],
        }}
      >
        <Icon name="send" size={18} color={sent ? colors.lime : colors.black} strokeWidth={2.4} />
      </View>
      <View style={{ flex: 1, gap: 2 }}>
        <Txt weight="bold">{transferTitle(transfer)}</Txt>
        <Txt size={fontSizes.xs} color={colors.muted}>
          {`${transferStatusLabel(transfer)} · ${formatDay((transfer.finishedAt ?? transfer.requestedAt).slice(0, 10))}`}
        </Txt>
      </View>
      <Txt weight="bold" color={sent ? colors.ink : colors.success}>
        {`${sent ? "" : "+"}${transferAmount(transfer)}`}
      </Txt>
    </Pressable>
  );
}
