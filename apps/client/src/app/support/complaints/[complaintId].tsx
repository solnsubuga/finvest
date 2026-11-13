import { useEffect, useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { Card, Notice } from "@/components/Blocks";
import { TextLink } from "@/components/Button";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { StackHeader } from "@/components/StackHeader";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { dateOf, plainDate } from "@/support/format";
import { complaintCategories, complaintOutcome, complaintStatus } from "@/support/labels";
import { useSupport } from "@/support/SupportProvider";
import { useNow } from "@/support/useNow";
import { Complaint, describeSupportError, SupportError } from "@/support/types";
import { colors, fontSizes } from "@/theme";

type Loaded =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "failed"; message: string }
  | { status: "ready"; complaint: Complaint };

/** One complaint: its reference, where it stands, the deadline and the outcome. */
export default function ComplaintScreen() {
  const router = useRouter();
  const { client } = useSupport();
  const { complaintId, made } = useLocalSearchParams<{ complaintId: string; made?: string }>();
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    client
      .getComplaint(complaintId)
      .then((complaint) => {
        if (live) {
          setLoaded({ status: "ready", complaint });
        }
      })
      .catch((error: unknown) => {
        if (!live) {
          return;
        }
        if (error instanceof SupportError && error.code === "not_found") {
          setLoaded({ status: "missing" });
        } else {
          setLoaded({ status: "failed", message: describeSupportError(error) });
        }
      });
    return () => {
      live = false;
    };
  }, [client, complaintId, attempt]);

  const nowMs = useNow();
  const complaint = loaded.status === "ready" ? loaded.complaint : null;
  const about = complaint
    ? complaintCategories.find((choice) => choice.category === complaint.category)?.title
    : null;

  return (
    <Screen signedIn>
      <Stack.Screen options={{ title: complaint ? `Complaint ${complaint.reference}` : "Complaint" }} />
      <StackHeader title="Complaint" fallback="/support/complaints" />
      <View style={{ marginTop: 20, gap: 14 }}>
        {loaded.status === "loading" ? <Loading label="Loading your complaint" /> : null}
        {loaded.status === "missing" ? (
          <Notice tone="neutral">We could not find this complaint.</Notice>
        ) : null}
        {loaded.status === "failed" ? (
          <LoadFailed
            message={loaded.message}
            onRetry={() => {
              setLoaded({ status: "loading" });
              setAttempt((value) => value + 1);
            }}
          />
        ) : null}
        {complaint ? (
          <>
            {made === "1" ? (
              <Notice tone="neutral">
                We have your complaint. Quote the reference below if you call or write to us.
              </Notice>
            ) : null}
            <View style={{ gap: 4 }}>
              <Eyebrow>Reference</Eyebrow>
              <Title>{complaint.reference}</Title>
            </View>
            <Card>
              <View style={{ gap: 12 }}>
                <Line label="Status" value={complaintStatus[complaint.status]} />
                <Line label="Made on" value={dateOf(complaint.receivedAt, nowMs)} />
                {complaint.status === "closed" && complaint.closedAt ? (
                  <Line label="Closed on" value={dateOf(complaint.closedAt, nowMs)} />
                ) : (
                  <Line label="We will answer by" value={plainDate(complaint.dueOn, nowMs)} />
                )}
              </View>
            </Card>
            {complaint.status === "closed" && complaint.outcome ? (
              <Card>
                <View style={{ gap: 6 }}>
                  <Eyebrow>Our answer</Eyebrow>
                  <Txt weight="bold">{complaintOutcome[complaint.outcome]}</Txt>
                  {complaint.resolution ? <Txt size={14}>{complaint.resolution}</Txt> : null}
                </View>
              </Card>
            ) : null}
            <Card>
              <View style={{ gap: 6 }}>
                <Eyebrow>What you told us</Eyebrow>
                {about ? (
                  <Txt size={fontSizes.sm} color={colors.muted}>
                    {`About: ${about}`}
                  </Txt>
                ) : null}
                <Txt weight="bold">{complaint.summary}</Txt>
                <Txt size={14}>{complaint.details}</Txt>
              </View>
            </Card>
            {complaint.status === "closed" ? (
              <Txt size={fontSizes.sm} color={colors.muted}>
                If you do not accept our answer, write to us in chat and quote this reference.
              </Txt>
            ) : null}
            <View style={{ alignItems: "flex-start" }}>
              <TextLink label="Write to us in chat" onPress={() => router.navigate("/help")} />
            </View>
          </>
        ) : null}
      </View>
    </Screen>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 12 }}>
      <Txt size={14} color={colors.muted}>
        {label}
      </Txt>
      <Txt size={14} weight="bold" style={{ flexShrink: 1, textAlign: "right" }}>
        {value}
      </Txt>
    </View>
  );
}
