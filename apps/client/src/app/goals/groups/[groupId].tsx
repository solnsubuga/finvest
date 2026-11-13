import { useCallback, useState } from "react";
import { Share, StyleSheet, View } from "react-native";
import * as Clipboard from "expo-clipboard";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { useGroups } from "@/goals/GoalsProvider";
import { GroupBar, GroupStanding, groupSubtitle } from "@/goals/GroupParts";
import {
  describeGroupError,
  Group,
  GroupMember,
  inviteLink,
  MAX_MEMBERS,
  NewInvite,
} from "@/goals/groups";
import { formatDateTime, formatDay, formatMoney } from "@/payments/format";
import { DetailList, Loading } from "@/payments/Parts";
import { colors } from "@/theme";

type State =
  | { status: "loading" }
  | { status: "ready"; group: Group }
  | { status: "error"; message: string };

export default function GroupDetail() {
  const router = useRouter();
  const client = useGroups();
  const { groupId } = useLocalSearchParams<{ groupId: string }>();
  const [state, setState] = useState<State>({ status: "loading" });
  // A link just made. Its code is shown this once: only its hash is kept.
  const [invite, setInvite] = useState<NewInvite | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState<"invite" | "stop" | "end" | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [confirmEnd, setConfirmEnd] = useState(false);

  const load = useCallback(() => {
    let live = true;
    client.getGroup(groupId).then(
      (group) => live && setState({ status: "ready", group }),
      (caught) => live && setState({ status: "error", message: describeGroupError(caught) }),
    );
    return () => {
      live = false;
    };
  }, [client, groupId]);

  useFocusEffect(load);

  const run = async (which: "invite" | "stop" | "end", work: () => Promise<void>) => {
    setBusy(which);
    setFailure(null);
    try {
      await work();
    } catch (caught) {
      setFailure(describeGroupError(caught));
    } finally {
      setBusy(null);
    }
  };

  const group = state.status === "ready" ? state.group : null;
  const open = group?.status === "active";
  const link = invite ? inviteLink(invite.code) : null;

  const makeInvite = () =>
    run("invite", async () => {
      const made = await client.newInvite(groupId);
      setInvite(made);
      setState({ status: "ready", group: await client.getGroup(groupId) });
    });

  const stopInvite = () =>
    run("stop", async () => {
      setInvite(null);
      setState({ status: "ready", group: await client.stopInvite(groupId) });
    });

  const end = () =>
    run("end", async () => {
      if (group?.isOwner) {
        setState({ status: "ready", group: await client.closeGroup(groupId) });
        setInvite(null);
      } else {
        await client.leaveGroup(groupId);
        router.replace("/goals");
      }
      setConfirmEnd(false);
    });

  const share = async () => {
    if (!link || !group) return;
    const message = `Join my Finvest group goal "${group.name}". Each of us saves into our own goal: ${link}`;
    try {
      await Share.share({ message });
    } catch {
      await copy();
    }
  };

  const copy = async () => {
    if (!link) return;
    await Clipboard.setStringAsync(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2_000);
  };

  return (
    <Screen
      signedIn
      footer={
        open && group ? (
          <Button
            label="Save to your part"
            onPress={() => router.push(`/goals/save/${group.ownGoalId}`)}
            variant="lime"
            testID="group-save"
          />
        ) : null
      }
    >
      <BackButton fallback="/goals" />
      {state.status === "loading" ? (
        <Loading label="Loading the group goal" />
      ) : state.status === "error" ? (
        <View style={{ marginTop: 20 }}>
          <Notice tone="danger">{state.message}</Notice>
        </View>
      ) : group ? (
        <View style={{ marginTop: 16, gap: 14 }}>
          <View style={{ gap: 4 }}>
            <Title>{group.name}</Title>
            <Txt size={13} color={colors.muted}>
              {groupSubtitle(group)}
            </Txt>
          </View>

          <View style={styles.hero} testID="group-hero">
            <View style={styles.heroHead}>
              <Txt size={13} color={colors.onBlackMuted}>
                Saved together
              </Txt>
              <GroupStanding group={group} onDark />
            </View>
            <View style={styles.amounts}>
              <Txt size={34} weight="extrabold" lineHeight={40} color={colors.white} style={{ letterSpacing: -0.8 }}>
                {formatMoney(group.value)}
              </Txt>
              <Txt size={13} color={colors.onBlackMuted}>
                {`of ${formatMoney(group.target).replace("UGX ", "")}`}
              </Txt>
            </View>
            <GroupBar group={group} onDark />
            <Txt size={12} color={colors.onBlackMuted}>
              {group.othersAsOf
                ? `Your part counts as it is now, everyone else's as of ${formatDay(group.othersAsOf)}. Figures are after tax.`
                : group.members.length > 1
                  ? "Your part counts as it is now. Everyone else's is added each morning."
                  : "Only your part so far. Invite others to save with you."}
            </Txt>
          </View>

          <DetailList rows={facts(group)} />

          <View style={styles.membersHead}>
            <Eyebrow>{`Members, ${group.members.length} of ${MAX_MEMBERS}`}</Eyebrow>
          </View>
          <Card padded={false}>
            {group.members.map((member, index) => (
              <MemberRow key={`${member.displayName}-${member.joinedAt}`} member={member} first={index === 0} />
            ))}
          </Card>
          <Notice tone="neutral">
            {group.members.length === 2
              ? "Members see each other's names and the combined total, never what anyone saves. With two of you, each can work out the other's total from it."
              : "Members see each other's names and the combined total, never what anyone saves."}
          </Notice>

          {open && group.isOwner ? (
            <View style={styles.box} testID="group-invite">
              <Txt weight="bold">Invite someone</Txt>
              {link ? (
                <>
                  <Txt size={13} color={colors.muted}>
                    {`Send this link by WhatsApp or SMS. It works until ${formatDateTime(invite?.expiresAt ?? "")}, and we show it only now.`}
                  </Txt>
                  <View style={styles.link}>
                    <Txt size={13} weight="bold" selectable testID="group-link">
                      {link}
                    </Txt>
                  </View>
                  <Button label="Share the link" onPress={share} height={48} testID="group-share" />
                  <TextLink label={copied ? "Copied" : "Copy the link"} onPress={copy} underline={false} />
                </>
              ) : group.inviteExpiresAt ? (
                <>
                  <Txt size={13} color={colors.muted}>
                    {`A link is open until ${formatDateTime(group.inviteExpiresAt)}. A link is shown only once, so make a new one to send it again. The old one stops working.`}
                  </Txt>
                  <Button label="Make a new link" onPress={makeInvite} loading={busy === "invite"} height={48} testID="group-new-link" />
                  <TextLink label="Stop the link" onPress={stopInvite} underline={false} />
                </>
              ) : (
                <>
                  <Txt size={13} color={colors.muted}>
                    {group.members.length >= MAX_MEMBERS
                      ? `The group has ${MAX_MEMBERS} people, which is the most at once.`
                      : "Make a link to send. Whoever opens it can join and choose their own part. It works for 7 days."}
                  </Txt>
                  {group.members.length < MAX_MEMBERS ? (
                    <Button label="Make an invite link" onPress={makeInvite} loading={busy === "invite"} height={48} testID="group-make-link" />
                  ) : null}
                </>
              )}
            </View>
          ) : null}

          {failure ? <Notice tone="danger">{failure}</Notice> : null}

          <TextLink label="Open your own goal" onPress={() => router.push(`/goals/${group.ownGoalId}`)} />

          {open ? (
            confirmEnd ? (
              <View style={styles.box} testID="group-end-confirm">
                <Txt weight="bold">{group.isOwner ? "Close this group goal?" : "Leave this group goal?"}</Txt>
                <Txt size={13} color={colors.muted}>
                  {group.isOwner
                    ? "Everyone's own goal stays open and keeps its savings. Nobody's money moves. A closed group cannot be opened again."
                    : "Your own goal stays open and keeps its savings, and the others no longer see your name or your part in the total. Nobody's money moves."}
                </Txt>
                <Button
                  label={group.isOwner ? "Close the group" : "Leave the group"}
                  onPress={end}
                  loading={busy === "end"}
                  height={48}
                  testID="group-end-yes"
                />
                <TextLink label="Keep it" onPress={() => setConfirmEnd(false)} underline={false} />
              </View>
            ) : (
              <TextLink
                label={group.isOwner ? "Close this group goal" : "Leave this group goal"}
                onPress={() => setConfirmEnd(true)}
              />
            )
          ) : null}
        </View>
      ) : null}
    </Screen>
  );
}

function facts(group: Group) {
  const rows = [{ label: "Target together", value: formatMoney(group.target) }];
  if (group.targetDate) rows.push({ label: "Needed by", value: formatDay(group.targetDate) });
  rows.push({ label: "Reached", value: `${group.percent}%` });
  if (group.status === "closed") rows.push({ label: "Status", value: "Closed" });
  return rows;
}

function MemberRow({ member, first }: { member: GroupMember; first: boolean }) {
  const role = [member.isYou ? "You" : null, member.isOwner ? "set it up" : null].filter(Boolean).join(", ");
  return (
    <View style={[styles.row, !first && styles.divider]}>
      <View style={styles.initial}>
        <Txt size={13} weight="extrabold">
          {member.displayName.slice(0, 1).toUpperCase()}
        </Txt>
      </View>
      <View style={{ flex: 1 }}>
        <Txt size={14} weight="bold">
          {member.displayName}
        </Txt>
        <Txt size={12} color={colors.muted}>
          {`${role ? `${role.charAt(0).toUpperCase()}${role.slice(1)}. ` : ""}Joined ${formatDay(member.joinedAt.slice(0, 10))}`}
        </Txt>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  hero: { backgroundColor: colors.black, borderRadius: 24, padding: 20, gap: 8 },
  heroHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  amounts: { flexDirection: "row", alignItems: "baseline", gap: 8, flexWrap: "wrap" },
  membersHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12, paddingHorizontal: 16 },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  initial: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: colors.limeSurface,
    alignItems: "center",
    justifyContent: "center",
  },
  box: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 16,
    gap: 10,
  },
  link: { backgroundColor: colors.paper, borderRadius: 12, padding: 12 },
});
