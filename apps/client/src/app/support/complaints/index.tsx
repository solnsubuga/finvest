import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useFocusEffect, useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { StackHeader } from "@/components/StackHeader";
import { Eyebrow, Txt } from "@/components/Txt";
import { dateOf, plainDate } from "@/support/format";
import { complaintStatus } from "@/support/labels";
import { useSupport } from "@/support/SupportProvider";
import { useNow } from "@/support/useNow";
import { Complaint, describeSupportError } from "@/support/types";
import { colors, fontSizes } from "@/theme";

type Loaded =
  | { status: "loading" }
  | { status: "failed"; message: string }
  | { status: "ready"; complaints: Complaint[] };

/** The client's own complaints, and the way to make one. */
export default function Complaints() {
  const router = useRouter();
  const { client } = useSupport();
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  // Read again each time the screen comes back in front, so a new complaint is listed.
  useFocusEffect(
    useCallback(() => {
      let live = true;
      client
        .listComplaints()
        .then((complaints) => {
          if (live) {
            setLoaded({ status: "ready", complaints });
          }
        })
        .catch((error: unknown) => {
          if (live) {
            setLoaded({ status: "failed", message: describeSupportError(error) });
          }
        });
      return () => {
        live = false;
      };
      // `attempt` restarts the read after a failure.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [client, attempt]),
  );

  const nowMs = useNow();

  return (
    <Screen
      signedIn
      footer={
        <Button
          label="Make a complaint"
          onPress={() => router.push("/support/complaints/new")}
          testID="new-complaint"
        />
      }
    >
      <Stack.Screen options={{ title: "Complaints" }} />
      <StackHeader title="Complaints" fallback="/help" />
      <View style={{ marginTop: 20, gap: 12 }}>
        <Txt size={14} color={colors.muted}>
          If something went wrong and we did not put it right, tell us here. A complaint gets a
          reference, and an answer within 5 working days.
        </Txt>
        {loaded.status === "loading" ? <Loading label="Loading your complaints" /> : null}
        {loaded.status === "failed" ? (
          <LoadFailed
            message={loaded.message}
            onRetry={() => {
              setLoaded({ status: "loading" });
              setAttempt((value) => value + 1);
            }}
          />
        ) : null}
        {loaded.status === "ready" && loaded.complaints.length === 0 ? (
          <Notice tone="neutral">You have not made a complaint.</Notice>
        ) : null}
        {loaded.status === "ready" && loaded.complaints.length > 0 ? (
          <Eyebrow>Your complaints</Eyebrow>
        ) : null}
        {loaded.status === "ready"
          ? loaded.complaints.map((complaint) => (
              <Pressable
                key={complaint.id}
                accessibilityRole="link"
                onPress={() =>
                  router.push({
                    pathname: "/support/complaints/[complaintId]",
                    params: { complaintId: complaint.id },
                  })
                }
                style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
                testID={`complaint-${complaint.reference}`}
              >
                <View style={{ flex: 1, gap: 2 }}>
                  <Txt size={fontSizes.sm} weight="semibold" color={colors.muted}>
                    {complaint.reference}
                  </Txt>
                  <Txt weight="bold">{complaint.summary}</Txt>
                  <Txt size={fontSizes.sm} color={colors.muted}>
                    {complaint.status === "closed" && complaint.closedAt
                      ? `Closed ${dateOf(complaint.closedAt, nowMs)}`
                      : `${complaintStatus[complaint.status]}. Answer by ${plainDate(complaint.dueOn, nowMs)}`}
                  </Txt>
                </View>
                <Icon name="chevron" size={16} strokeWidth={2.2} />
              </Pressable>
            ))
          : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 18,
    padding: 16,
  },
});
