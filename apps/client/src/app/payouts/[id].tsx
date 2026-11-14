import { useCallback, useEffect, useState } from "react";
import { Platform, Pressable, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import { LoadFailed } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { formatDateTime, formatFileSize } from "@/payments/format";
import { Loading, Step, Timeline } from "@/payments/Parts";
import { chooseReceiptFile, photographReceipt } from "@/payments/pickReceipt";
import { checkReceipt, PickedFile, receiptProblemCopy } from "@/payments/receipt";
import { ReceiptFile } from "@/payments/types";
import { accountProvider, accountStatusPill, canProveAgain, isPayable, proofKindCopy, rejectionCopy } from "@/payouts/format";
import { NAME_DECISION_READ_AGAIN_MS } from "@/payouts/timing";
import { Pill, useFocusLoaded } from "@/payouts/Parts";
import { usePayouts } from "@/payouts/PayoutsProvider";
import { describePayoutsError, PayoutAccount, PayoutsClient, PayoutsError, PayoutsErrorCode, ProofKind } from "@/payouts/types";
import { colors, fontSizes, radii } from "@/theme";

/**
 * One payout account and its next step: the texted code, then proof for a bank account, then
 * our check. Each step is the client's own or ours, and the screen says which.
 */
export default function PayoutAccountScreen() {
  const router = useRouter();
  const { id, sent } = useLocalSearchParams<{ id: string; sent?: string }>();
  const client = usePayouts();
  const [loaded, retry, replace] = useFocusLoaded(useCallback(() => client.getAccount(id), [client, id]));
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const remove = async () => {
    setRemoving(true);
    setRemoveError(null);
    try {
      await client.removeAccount(id);
      router.replace("/payouts");
    } catch (caught) {
      // Removing twice is harmless, so a lost answer can simply be tried again.
      setRemoveError(describePayoutsError(caught));
      setRemoving(false);
    }
  };

  const account = loaded.state === "ready" ? loaded.value : null;
  return (
    <Screen signedIn>
      <Stack.Screen options={{ title: account ? accountProvider(account) : "Payout account" }} />
      <BackButton fallback="/payouts" />
      {loaded.state === "loading" ? (
        <Loading label="Loading your payout account" />
      ) : loaded.state === "failed" ? (
        <View style={{ marginTop: 20 }}>
          <LoadFailed message={loaded.message} onRetry={retry} />
        </View>
      ) : (
        <View style={{ marginTop: 20, gap: 18 }}>
          <Summary account={loaded.value} nowMs={loaded.readAtMs} />
          <Timeline steps={steps(loaded.value, loaded.readAtMs)} />
          <NextStep account={loaded.value} nowMs={loaded.readAtMs} codeSent={sent === "1"} onChanged={replace} />
          <View style={{ gap: 10, marginTop: 8 }}>
            {confirmRemove ? (
              <Card>
                <View style={{ gap: 12 }} testID="remove-confirm">
                  <Txt weight="bold">Remove this account?</Txt>
                  <Txt size={fontSizes.sm} color={colors.muted}>
                    It is removed for good and cannot be used again. Money already on its way is not stopped. To use
                    it later, add it again and prove it again.
                  </Txt>
                  {removeError ? <Notice tone="danger">{removeError}</Notice> : null}
                  <Button label="Remove it" onPress={remove} loading={removing} testID="remove-account" />
                  <TextLink label="Keep it" onPress={() => setConfirmRemove(false)} />
                </View>
              </Card>
            ) : (
              <TextLink label="Remove this account" onPress={() => setConfirmRemove(true)} />
            )}
          </View>
        </View>
      )}
    </Screen>
  );
}

function Summary({ account, nowMs }: { account: PayoutAccount; nowMs: number }) {
  const pill = accountStatusPill(account, nowMs);
  return (
    <View style={{ gap: 6 }}>
      <Eyebrow>{account.kind === "bank" ? "Bank account" : "Mobile money"}</Eyebrow>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Title>{accountProvider(account)}</Title>
        <Pill {...pill} />
      </View>
      <Txt size={fontSizes.md} weight="semibold">
        {account.account}
      </Txt>
      <Txt size={fontSizes.sm} color={colors.muted}>
        {`In the name of ${account.holderName}`}
      </Txt>
    </View>
  );
}

/** The four steps to being paid, as docs/payouts.md lists them. */
function steps(account: PayoutAccount, nowMs: number): Step[] {
  const order = ["awaiting_code", "awaiting_proof", "in_review", "approved"] as const;
  const at = account.status === "rejected" ? 2 : order.indexOf(account.status);
  const payable = isPayable(account, nowMs);
  const state = (index: number, ours: boolean): Step["state"] => (index < at ? "done" : index === at && ours ? "now" : "later");
  return [
    { label: "Code entered", state: state(0, false), note: at === 0 ? "Your turn" : undefined },
    account.kind === "bank"
      ? { label: "Proof sent", state: state(1, false), note: at === 1 ? "Your turn" : undefined }
      : {
          label: "Name checked with the network",
          // Only a lookup in flight is ours. One that went unanswered waits for the client to ask
          // again, and one at the limit has stopped, so neither shows as moving.
          state: state(1, !account.nameCheck || account.nameCheck === "checking"),
          note: at === 1 && account.nameCheck === "not_answered" ? "Your turn" : undefined,
        },
    {
      label: account.status === "rejected" ? "Not approved" : "Checked by our team",
      state: account.status === "rejected" ? "later" : state(2, true),
    },
    {
      label: "Ready to be paid",
      state: payable ? "done" : at === 3 ? "now" : "later",
      note: at === 3 && !payable && account.payableFrom ? `From ${formatDateTime(account.payableFrom)}` : undefined,
    },
  ];
}

type NextProps = { account: PayoutAccount; codeSent: boolean; onChanged: (account: PayoutAccount) => void };

function NextStep({ account, nowMs, codeSent, onChanged }: NextProps & { nowMs: number }) {
  switch (account.status) {
    case "awaiting_code":
      return <EnterCode account={account} codeSent={codeSent} onChanged={onChanged} />;
    case "awaiting_proof":
      return account.kind === "bank" ? (
        <SendProof account={account} onChanged={onChanged} />
      ) : (
        <NameCheckStep account={account} onChanged={onChanged} />
      );
    case "in_review":
      return (
        <Notice tone="neutral">
          Our team is checking your proof against the name on your ID. We will tell you when it is done.
        </Notice>
      );
    case "approved":
      return isPayable(account, nowMs) ? (
        <Notice tone="neutral">Approved. You can choose this account when an investment ends or when you withdraw.</Notice>
      ) : (
        <Notice tone="neutral">
          {`Approved. For your safety a new account can be paid one day after we approve it${
            account.payableFrom ? `, so from ${formatDateTime(account.payableFrom)}` : ""
          }. You can already choose it for an investment's maturity.`}
        </Notice>
      );
    case "rejected":
      return (
        <View style={{ gap: 14 }}>
          <Notice tone="danger">
            {account.rejectionReason ? rejectionCopy[account.rejectionReason] : "We could not approve this account."}
          </Notice>
          {account.kind === "bank" && canProveAgain(account.rejectionReason) ? (
            <SendProof account={account} onChanged={onChanged} again />
          ) : null}
        </View>
      );
  }
}

/** A mobile money number waiting for the name its network holds to be read and compared. */
function NameCheckStep({ account, onChanged }: Omit<NextProps, "codeSent">) {
  const client = usePayouts();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Asked again once the ID was approved: the comparison is queued, so the answer still says
  // waiting. Show that it is under way and read the account again shortly.
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    if (!comparing) return;
    const timer = setTimeout(async () => {
      try {
        onChanged(await client.getAccount(account.id));
      } catch {
        // Could not read it: show the card again, so the client can ask once more.
      }
      setComparing(false);
    }, NAME_DECISION_READ_AGAIN_MS);
    return () => clearTimeout(timer);
  }, [comparing, client, account.id, onChanged]);

  const again = async () => {
    setBusy(true);
    setError(null);
    try {
      const answered = await client.checkNameAgain(account.id);
      onChanged(answered);
      if (answered.status === "awaiting_proof" && answered.nameCheck === "waiting_for_id") setComparing(true);
    } catch (caught) {
      // These say the screen is out of date: show where the account is instead.
      if (!(await movedOn(caught, client, account.id, onChanged, STALE_NAME_CHECK))) {
        setError(describePayoutsError(caught));
      }
    } finally {
      setBusy(false);
    }
  };

  if (comparing) {
    return (
      <View testID="name-check-comparing">
        <Notice tone="neutral">
          We are comparing the name the network holds with the name on your ID now. This takes a few seconds.
        </Notice>
      </View>
    );
  }

  switch (account.nameCheck) {
    case "not_answered":
      return (
        <Card>
          <View style={{ gap: 14 }} testID="name-check-not-answered">
            <Txt weight="bold">The network did not answer</Txt>
            <Txt size={fontSizes.sm} color={colors.muted}>
              We asked the network for the name this number is registered in, but no answer came. Check again and we will
              ask once more.
            </Txt>
            {error ? <Notice tone="danger">{error}</Notice> : null}
            <Button label="Check again" onPress={again} loading={busy} testID="name-check-again" />
          </View>
        </Card>
      );
    case "waiting_for_id":
      return (
        <Card>
          <View style={{ gap: 14 }} testID="name-check-waiting-for-id">
            <Txt weight="bold">Waiting for your ID check</Txt>
            <Txt size={fontSizes.sm} color={colors.muted}>
              The network told us the name on this number. We compare it with the name on your ID once your ID check is
              approved. When it is, come back and check again.
            </Txt>
            {error ? <Notice tone="danger">{error}</Notice> : null}
            <Button label="Check again" onPress={again} loading={busy} testID="name-check-again" />
          </View>
        </Card>
      );
    case "limit_reached":
      return (
        <Notice tone="warning">
          We have asked the network for this number&apos;s name several times and no answer came. Remove the number and add
          it again later, or use another account.
        </Notice>
      );
    default:
      return (
        <Notice tone="neutral">
          We are checking the name the network holds for this number against the name on your ID. We will tell you when
          it is approved.
        </Notice>
      );
  }
}

function EnterCode({ account, codeSent, onChanged }: NextProps) {
  const client = usePayouts();
  const [sent, setSent] = useState(codeSent);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"send" | "verify" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wrong, setWrong] = useState(false);

  const send = async () => {
    setBusy("send");
    setError(null);
    try {
      await client.sendCode(account.id);
      setSent(true);
      setCode("");
      setWrong(false);
    } catch (caught) {
      setError(describePayoutsError(caught));
    }
    setBusy(null);
  };

  const verify = async () => {
    setBusy("verify");
    setError(null);
    try {
      onChanged(await client.verifyCode(account.id, code));
    } catch (caught) {
      if (await movedOn(caught, client, account.id, onChanged)) return;
      const reason = caught instanceof PayoutsError ? caught.code : "unavailable";
      setWrong(reason === "invalid_code");
      if (reason === "invalid_code" || reason === "code_expired") setCode("");
      setError(describePayoutsError(caught));
      setBusy(null);
    }
  };

  const where = account.kind === "mobile_money" ? `to ${account.account}` : "to your own mobile number";
  return (
    <Card>
      <View style={{ gap: 14 }}>
        <Txt weight="bold">{sent ? `We texted a code ${where}` : "Prove the account is yours"}</Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {sent
            ? `Enter the ${CODE_LENGTH} digits. The code works for ten minutes.`
            : `We text a code ${where}. Enter it here to go on.`}
        </Txt>
        {sent ? (
          <>
            <CodeInput value={code} onChangeText={setCode} invalid={wrong} />
            {error ? <Notice tone="danger">{error}</Notice> : null}
            <Button
              label="Confirm"
              onPress={verify}
              loading={busy === "verify"}
              disabled={code.length !== CODE_LENGTH || busy === "send"}
              testID="payout-code-submit"
            />
            <TextLink label={busy === "send" ? "Sending…" : "Send a new code"} onPress={send} />
          </>
        ) : (
          <>
            {error ? <Notice tone="danger">{error}</Notice> : null}
            <Button label="Text me the code" onPress={send} loading={busy === "send"} testID="payout-code-send" />
          </>
        )}
      </View>
    </Card>
  );
}

/** When the account is no longer at the step shown, reads it again and shows where it is. */
const STALE_NAME_CHECK: readonly PayoutsErrorCode[] = ["moved_on", "name_check_in_progress", "name_check_limit_reached"];

async function movedOn(
  caught: unknown,
  client: PayoutsClient,
  accountId: string,
  onChanged: (account: PayoutAccount) => void,
  codes: readonly PayoutsErrorCode[] = ["moved_on"],
): Promise<boolean> {
  if (!(caught instanceof PayoutsError) || !codes.includes(caught.code)) return false;
  try {
    onChanged(await client.getAccount(accountId));
    return true;
  } catch {
    return false;
  }
}

function SendProof({ account, onChanged, again = false }: { account: PayoutAccount; onChanged: (account: PayoutAccount) => void; again?: boolean }) {
  const client = usePayouts();
  const [proofKind, setProofKind] = useState<ProofKind>("bank_statement");
  const [file, setFile] = useState<ReceiptFile | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const take = (picked: PickedFile | null) => {
    if (!picked) return;
    const checked = checkReceipt(picked);
    if (typeof checked === "string") {
      setFile(null);
      setProblem(receiptProblemCopy[checked]);
    } else {
      setFile(checked);
      setProblem(null);
    }
  };

  const photograph = async () => {
    const result = await photographReceipt();
    if (result === "no_permission") {
      setProblem("Allow the camera in your phone's settings, or choose a file instead.");
      return;
    }
    take(result);
  };

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setProblem(null);
    try {
      onChanged(await client.uploadProof(account.id, proofKind, file));
    } catch (caught) {
      // If an earlier try went through after all, the account has moved on: show where it is.
      if (await movedOn(caught, client, account.id, onChanged)) return;
      setProblem(describePayoutsError(caught));
      setBusy(false);
    }
  };

  return (
    <Card>
      <View style={{ gap: 14 }}>
        <Txt weight="bold">{again ? "Send new proof" : "Send proof the account is yours"}</Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          It must show your name as on your ID and this account number. Only our team sees it.
        </Txt>
        <View style={{ gap: 8 }} accessibilityRole="radiogroup">
          {(Object.keys(proofKindCopy) as ProofKind[]).map((kind) => {
            const on = kind === proofKind;
            return (
              <Pressable
                key={kind}
                accessibilityRole="radio"
                aria-checked={on}
                onPress={() => setProofKind(kind)}
                style={[styles.option, on && styles.selected]}
                testID={`proof-${kind}`}
              >
                <View style={[styles.radio, on && styles.radioOn]} />
                <View style={{ flex: 1, gap: 2 }}>
                  <Txt weight="bold">{proofKindCopy[kind].title}</Txt>
                  <Txt size={fontSizes.sm} color={colors.muted}>
                    {proofKindCopy[kind].detail}
                  </Txt>
                </View>
              </Pressable>
            );
          })}
        </View>
        {file ? (
          <View style={styles.file} testID="proof-file">
            <Txt weight="semibold" numberOfLines={1}>
              {file.name}
            </Txt>
            <Txt size={fontSizes.sm} color={colors.muted}>
              {`${formatFileSize(file.sizeBytes)}, ready to send`}
            </Txt>
          </View>
        ) : null}
        {problem ? <Notice tone="danger">{problem}</Notice> : null}
        {file ? (
          <Button label="Send to Finvest" onPress={upload} loading={busy} testID="proof-upload" />
        ) : null}
        <View style={{ flexDirection: "row", gap: 24, flexWrap: "wrap" }}>
          <TextLink
            label={file ? "Choose another file" : "Choose a file"}
            onPress={async () => take(await chooseReceiptFile())}
          />
          {Platform.OS === "web" ? null : <TextLink label="Take a photo" onPress={photograph} />}
        </View>
        <Txt size={fontSizes.xs} color={colors.muted}>
          PDF, JPG or PNG, up to 10 MB.
        </Txt>
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 14,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  selected: { borderColor: colors.black, borderWidth: 2, padding: 13 },
  radio: { width: 20, height: 20, borderRadius: 10, borderWidth: 2, borderColor: colors.borderStrong },
  radioOn: { borderColor: colors.black, borderWidth: 6 },
  file: { padding: 14, borderRadius: radii.lg, backgroundColor: colors.surfaceSunken, gap: 2 },
});
