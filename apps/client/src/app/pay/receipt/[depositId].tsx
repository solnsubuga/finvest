import { useEffect, useState } from "react";
import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useLocalSearchParams, useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { DocumentLink, useDocumentFor } from "@/documents/DocumentLink";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Txt } from "@/components/Txt";
import { earningFrom } from "@/investing/format";
import { useInvesting } from "@/investing/InvestingProvider";
import { formatDateTime, formatMoney, networkName, networkProduct } from "@/payments/format";
import { goalSavingFor } from "@/payments/payingFor";
import { Detail, DetailList, Loading, ShareNote } from "@/payments/Parts";
import { asText, shareText, ShareOutcome } from "@/payments/share";
import { useDeposit } from "@/payments/useDeposit";
import { colors, fontSizes } from "@/theme";

/** The receipt for a confirmed payment. Anything not yet confirmed goes back to the status screen. */
export default function Receipt() {
  const router = useRouter();
  const { depositId } = useLocalSearchParams<{ depositId: string }>();
  const { deposit, missing, stale } = useDeposit(depositId);
  const pdf = useDocumentFor("receipt", depositId);
  const goal = deposit ? goalSavingFor(deposit.payable.id) : null;
  // A saving goes back to its goal, where the client was. Anything else goes Home.
  const done = () => (goal ? router.dismissTo(`/goals/${goal.goalId}`) : router.replace("/"));
  const [shared, setShared] = useState<ShareOutcome | null>(null);
  // The investment starts on the Kampala business day its payment was confirmed, and earns
  // from the day after, so a weekend payment does not earn tomorrow.
  const investing = useInvesting();
  const [startDate, setStartDate] = useState<string | null>(null);
  // A bond holding starts the same way, but has no interest earning from a start date to show.
  const investmentId = deposit?.status === "confirmed" && deposit.payable.kind !== "bond" ? deposit.payable.id : null;
  useEffect(() => {
    if (!investmentId) return;
    let live = true;
    investing
      .getInvestment(investmentId)
      .then((investment) => {
        if (live) setStartDate(investment.startDate);
      })
      .catch(() => {
        // The receipt stands without it, and says so in general words.
      });
    return () => {
      live = false;
    };
  }, [investing, investmentId]);

  if (missing) {
    return (
      <Screen signedIn footer={<Button label="Close" onPress={done} />}>
        <Stack.Screen options={{ title: "Receipt" }} />
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">We could not find this payment.</Notice>
        </View>
      </Screen>
    );
  }

  if (!deposit) {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: "Receipt" }} />
        {stale ? (
          <Notice tone="neutral">We could not reach Finvest. Trying again.</Notice>
        ) : (
          <Loading label="Loading your receipt" />
        )}
      </Screen>
    );
  }

  if (deposit.status !== "confirmed" || !deposit.confirmedAt) {
    return <Redirect href={`/pay/deposit/${deposit.id}`} />;
  }

  const network = networkName[deposit.network];
  const earning = earningFrom(startDate);
  // A saving's title already leads with its goal ("Daniel's school fees: 91 Day Treasury Bill").
  const product = goal && deposit.payable.title.startsWith(`${goal.goalName}: `)
    ? deposit.payable.title.slice(goal.goalName.length + 2)
    : deposit.payable.title;
  const rows: Detail[] = [
    { label: "Date and time", value: formatDateTime(deposit.confirmedAt) },
    { label: "Paid from", value: networkProduct[deposit.network] },
    { label: "Number", value: deposit.payer },
  ];
  if (deposit.networkReference) {
    rows.push({ label: `${network} reference`, value: deposit.networkReference });
  }
  rows.push({ label: "Finvest reference", value: deposit.reference });
  // The number is already masked as the PDF receipt masks it.
  const share = async () =>
    setShared(
      await shareText(
        "Finvest receipt",
        asText(
          goal
            ? `Finvest receipt: ${formatMoney(deposit.amount)} saved for ${goal.goalName}, in ${product}`
            : `Finvest receipt: ${formatMoney(deposit.amount)} paid into ${deposit.payable.title}`,
          rows,
        ),
      ),
    );

  return (
    <Screen
      signedIn
      footer={
        <>
          <Button label="Share receipt" variant="secondary" onPress={share} testID="receipt-share" />
          <Button label={goal ? "Back to your goal" : "Done"} onPress={done} testID="receipt-done" />
          <TextLink
            label="Something wrong with this?"
            onPress={() => router.push({ pathname: "/support/complaints/new", params: { payment: deposit.reference } })}
            testID="receipt-complain"
          />
        </>
      }
    >
      <Stack.Screen options={{ title: "Receipt" }} />
      <View style={styles.top}>
        <View style={styles.tick}>
          <Icon name="check" size={30} color={colors.black} strokeWidth={2.6} />
        </View>
        <Txt size={fontSizes.sm} weight="bold" color={colors.success}>
          Completed
        </Txt>
        <Txt
          accessibilityRole="header"
          size={34}
          weight="extrabold"
          lineHeight={40}
          style={{ letterSpacing: -0.8 }}
        >
          {formatMoney(deposit.amount)}
        </Txt>
        <Txt color={colors.muted} style={{ textAlign: "center" }}>
          {goal ? `Saved for ${goal.goalName}, in ${product}` : `Paid into ${deposit.payable.title}`}
        </Txt>
      </View>
      <View style={{ marginTop: 20 }}>
        <Notice tone="neutral">
          {goal
            ? `Your money is in. It is invested in ${product} and ${earning}. You can follow it on ${goal.goalName}.`
            : deposit.payable.kind === "bond"
              ? `Your money is in. Your part of the ${deposit.payable.title} is bought, and its coupons are paid every six months. You can follow it in Portfolio.`
              : `Your money is in. It is invested in ${deposit.payable.title} and ${earning}. You can follow it in Portfolio.`}
        </Notice>
      </View>
      <View style={{ marginTop: 24 }}>
        <DetailList rows={rows} />
      </View>
      <ShareNote outcome={shared} />
      {pdf ? (
        <View style={{ marginTop: 12, gap: 8 }}>
          <DocumentLink document={pdf} title="Receipt (PDF)" testID="receipt-pdf" />
          <Txt size={fontSizes.sm} color={colors.muted}>
            It is also emailed to you if you have proved your email address.
          </Txt>
        </View>
      ) : (
        <View style={{ marginTop: 16, gap: 4 }}>
          <Txt size={fontSizes.sm} color={colors.muted}>
            A PDF of this receipt is kept in Documents, and emailed to you if you have proved your email address.
          </Txt>
          <TextLink label="Open Documents" onPress={() => router.push("/documents")} />
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { marginTop: 32, alignItems: "center", gap: 6 },
  tick: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 6,
  },
});
