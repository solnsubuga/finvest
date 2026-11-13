import { useEffect, useState } from "react";
import { Platform, StyleSheet, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { groupDigits, parseShillings } from "@/goals/amount";
import { useGroups } from "@/goals/GoalsProvider";
import {
  describeGroupError,
  groupOutcomeUnknown,
  InvitePreview,
  MAX_DISPLAY_NAME,
  readInviteCode,
} from "@/goals/groups";
import { categoryCopy } from "@/goals/types";
import { newIdempotencyKey } from "@/payments/idempotency";
import { formatDay, formatMoney } from "@/payments/format";
import { DetailList } from "@/payments/Parts";
import { colors } from "@/theme";

/** On the web an invite link carries its code after #, which never reaches a server. */
function codeFromLink(): string {
  if (Platform.OS !== "web" || typeof window === "undefined") return "";
  return window.location.hash.replace(/^#/, "");
}

type Problems = Partial<Record<"code" | "part" | "you", string>>;

/** Join a group goal with an invite link or code. */
export default function JoinGroup() {
  const router = useRouter();
  const client = useGroups();
  const params = useLocalSearchParams<{ code?: string }>();
  const [arrived] = useState(() => readInviteCode(params.code ?? codeFromLink()));
  const [code, setCode] = useState(() => params.code ?? codeFromLink());
  const [preview, setPreview] = useState<{ code: string; invite: InvitePreview } | null>(null);
  const [part, setPart] = useState("");
  const [you, setYou] = useState("");
  const [problems, setProblems] = useState<Problems>({});
  // A link that opened the app is looked up straight away.
  const [busy, setBusy] = useState(arrived !== null);
  const [failure, setFailure] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey);
  const [locked, setLocked] = useState(false);

  const lookUp = async (text: string = code) => {
    const found = readInviteCode(text);
    if (!found) {
      setProblems({ code: "Paste the whole link, or the 22-character code from it." });
      return;
    }
    setProblems({});
    setBusy(true);
    setFailure(null);
    try {
      setPreview({ code: found, invite: await client.previewInvite(found) });
    } catch (caught) {
      setFailure(describeGroupError(caught));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!arrived) return;
    let live = true;
    client.previewInvite(arrived).then(
      (found) => live && setPreview({ code: arrived, invite: found }),
      (caught) => live && setFailure(describeGroupError(caught)),
    ).finally(() => live && setBusy(false));
    return () => {
      live = false;
    };
  }, [arrived, client]);

  const join = async () => {
    if (!preview) return;
    const own = parseShillings(part);
    const cleanYou = you.trim().replace(/\s+/g, " ");
    const found: Problems = {};
    if (!own) found.part = "Enter your own part, in shillings.";
    if (!cleanYou) found.you = "Enter the name the others will see.";
    else if (cleanYou.length > MAX_DISPLAY_NAME) found.you = `Keep it to ${MAX_DISPLAY_NAME} characters.`;
    setProblems(found);
    if (Object.keys(found).length > 0 || !own) return;
    setBusy(true);
    setFailure(null);
    try {
      const group = await client.joinGroup(
        preview.code,
        { displayName: cleanYou, ownTarget: { currency: "UGX", minor: own } },
        key,
      );
      router.replace(`/goals/groups/${group.id}`);
    } catch (caught) {
      if (groupOutcomeUnknown(caught)) setLocked(true);
      setFailure(describeGroupError(caught));
    } finally {
      setBusy(false);
    }
  };

  const invite = preview?.invite;

  return (
    <Screen
      signedIn
      footer={
        invite ? (
          <Button label="Join the group goal" onPress={join} loading={busy} variant="lime" testID="group-join" />
        ) : (
          <Button label="Look it up" onPress={() => lookUp()} loading={busy} testID="group-look-up" />
        )
      }
    >
      <BackButton fallback="/goals" />
      <View style={{ marginTop: 20, gap: 8 }}>
        <Title>{invite ? invite.name : "Join a group goal"}</Title>
        <Txt color={colors.muted}>
          {invite
            ? `${invite.ownerName} set this up. ${invite.members === 1 ? "1 person is" : `${invite.members} people are`} saving towards it.`
            : "Paste the link someone sent you, or the code from it."}
        </Txt>
      </View>

      {invite ? (
        <View style={{ marginTop: 18, gap: 16 }} testID="group-preview">
          <DetailList
            rows={[
              { label: "For", value: categoryCopy[invite.category].name },
              { label: "Target together", value: formatMoney(invite.target) },
              ...(invite.targetDate ? [{ label: "Needed by", value: formatDay(invite.targetDate) }] : []),
            ]}
          />
          <Notice tone="neutral">
            {`You save into a goal of your own, invested in your name, and you can leave at any time with your savings. The others see the name you choose and the group's combined total, which includes your savings, never what you save on its own.${
              invite.members === 1
                ? ` With just you and ${invite.ownerName}, each of you can work out the other's total from it.`
                : ""
            }`}
          </Notice>
          <TextField
            label="Your part"
            hint="The target of your own goal. You can change it later."
            value={part}
            onChangeText={(text) => setPart(groupDigits(text))}
            keyboardType="number-pad"
            inputMode="numeric"
            placeholder="2,000,000"
            error={problems.part}
            editable={!locked}
            testID="join-part"
          />
          <TextField
            label="Your name in the group"
            value={you}
            onChangeText={setYou}
            placeholder="Daniel"
            maxLength={40}
            error={problems.you}
            editable={!locked}
            testID="join-you"
          />
        </View>
      ) : (
        <View style={{ marginTop: 18 }}>
          <TextField
            label="Link or code"
            value={code}
            onChangeText={setCode}
            placeholder="https://app.finvestug.com/goals/groups/join#..."
            autoCapitalize="none"
            autoCorrect={false}
            error={problems.code}
            testID="join-code"
          />
        </View>
      )}

      <View style={styles.after}>
        {failure ? <Notice tone="danger">{failure}</Notice> : null}
        {locked ? (
          <Notice tone="warning">
            We did not hear back. Pressing again sends the same request, so you never join twice.
          </Notice>
        ) : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  after: { marginTop: 12, gap: 10 },
});
