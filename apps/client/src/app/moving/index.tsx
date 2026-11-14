import { ReactNode, useState } from "react";
import { View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { TextField } from "@/components/Field";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { useClaimClient } from "@/claim/ClaimProvider";
import {
  Claim,
  ClaimCheck,
  ClaimError,
  ClaimPhotoKind,
  CLAIM_PHOTOS,
  describeClaimError,
  MAX_PROBLEM_LENGTH,
} from "@/claim/types";
import { useClaim } from "@/claim/useClaim";
import { Badge, Progress, StatusLine } from "@/identity/Parts";
import { colors, fontSizes } from "@/theme";

const HOME = "/";
const TITLE = "Moving to Finvest 2.0";

const photoNames: Record<ClaimPhotoKind, string> = { front: "Front of your ID", selfie: "Selfie" };

type Stage = "account" | "identity" | "investments";

/** The three steps of moving over, and where the client is. */
function Steps({ now, done = false }: { now: Stage; done?: boolean }) {
  const order: Stage[] = ["account", "identity", "investments"];
  const at = order.indexOf(now);
  const state = (stage: Stage): Progress => {
    const index = order.indexOf(stage);
    if (done || index < at) return "done";
    return index === at ? "now" : "next";
  };
  return (
    <Card>
      <View style={{ gap: 14 }}>
        <StatusLine state={state("account")} title="Open your account" detail="The code and your new password" />
        <StatusLine state={state("identity")} title="Confirm it is you" detail="So nobody else can open it" />
        <StatusLine
          state={state("investments")}
          title="Check your investments"
          detail="The ones we carried over from Finvest 1.0"
        />
      </View>
    </Card>
  );
}

function Page({ title, intro, footer, children }: { title: string; intro: string; footer?: ReactNode; children?: ReactNode }) {
  return (
    <Screen signedIn footer={footer}>
      <Stack.Screen options={{ title: TITLE }} />
      <BackButton fallback={HOME} />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>{title}</Title>
        <Txt color={colors.muted}>{intro}</Txt>
      </View>
      <View style={{ marginTop: 20, gap: 14 }}>{children}</View>
    </Screen>
  );
}

/** A photo of the claim check: taken or not, and the way to take it. */
function PhotoRow({ kind, taken, onPress }: { kind: ClaimPhotoKind; taken: boolean; onPress: () => void }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
      <View style={{ flex: 1 }}>
        <StatusLine state={taken ? "done" : "now"} title={photoNames[kind]} detail={taken ? "Taken" : "Not taken yet"} />
      </View>
      <TextLink label={taken ? "Take again" : "Take"} onPress={onPress} testID={`claim-photo-${kind}`} />
    </View>
  );
}

function missingWords(missing: string[]): string {
  const names = missing.map((item) => photoNames[item as ClaimPhotoKind]?.toLowerCase() ?? "your ID check");
  return `Still needed: ${names.join(", ")}.`;
}

/** Finvest 1.0 checked their ID: a photo of its front and a selfie, matched by a person. */
function Selfie({ check, onChanged }: { check: ClaimCheck | null; onChanged: (claim: Claim | null, check: ClaimCheck | null) => void }) {
  const router = useRouter();
  const client = useClaimClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (check?.status === "not_matched") {
    return (
      <Page
        title="We could not confirm it is you"
        intro="The person who compared your photos could not match them to the ID Finvest 1.0 checked. We will contact you to sort it out."
        footer={<Button label="Write to us" variant="secondary" onPress={() => router.push("/help")} />}
      >
        <Steps now="identity" />
      </Page>
    );
  }

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const started = await client.startCheck();
      router.push(`/moving/photo/${started.missing[0] ?? "front"}`);
    } catch (caught) {
      setError(describeClaimError(caught));
    } finally {
      setBusy(false);
    }
  };

  const handIn = async () => {
    setBusy(true);
    setError(null);
    try {
      const claim = await client.handIn();
      onChanged(claim, await client.getCheck());
    } catch (caught) {
      setError(
        caught instanceof ClaimError && caught.code === "incomplete" && caught.missing.length > 0
          ? missingWords(caught.missing)
          : describeClaimError(caught),
      );
      setBusy(false);
    }
  };

  const ready = check !== null && check.missing.length === 0;
  const footer = !check ? (
    <Button label="Take the photos" onPress={start} loading={busy} testID="claim-photos-start" />
  ) : ready ? (
    <Button label="Send for checking" onPress={handIn} loading={busy} testID="claim-hand-in" />
  ) : (
    <Button
      label="Take the next photo"
      onPress={() => router.push(`/moving/photo/${check.missing[0]}`)}
      testID="claim-next-photo"
    />
  );

  return (
    <Page
      title="Confirm it is you"
      intro="Finvest 1.0 already checked your ID. Take a photo of the front of that ID and a selfie, and a person at Finvest compares them."
      footer={footer}
    >
      <Steps now="identity" />
      {error ? <Notice tone="danger">{error}</Notice> : null}
      {check ? (
        <Card>
          <View style={{ gap: 14 }}>
            {CLAIM_PHOTOS.map((kind) => (
              <PhotoRow
                key={kind}
                kind={kind}
                taken={check.photos.includes(kind)}
                onPress={() => router.push(`/moving/photo/${kind}`)}
              />
            ))}
          </View>
        </Card>
      ) : null}
      <Txt size={fontSizes.sm} color={colors.muted}>
        You can already see your investments. Withdrawals and new investments open once we have
        confirmed it is you.
      </Txt>
    </Page>
  );
}

/** Finvest 1.0 never checked their ID, so they take the full ID check. */
function FullCheck() {
  const router = useRouter();
  return (
    <Page
      title="Confirm who you are"
      intro="Finvest 1.0 did not keep a checked ID for you, so we need one now. It takes about two minutes: photos of your ID, a selfie and your details."
      footer={<Button label="Go to the ID check" onPress={() => router.push("/id-check")} testID="claim-id-check" />}
    >
      <Steps now="identity" />
      <Txt size={fontSizes.sm} color={colors.muted}>
        You can already see your investments. Withdrawals and new investments open once we have
        confirmed who you are.
      </Txt>
    </Page>
  );
}

/** Staff asked for named photos of the claim check again. */
function Retake({ check, onChanged }: { check: ClaimCheck; onChanged: (claim: Claim | null, check: ClaimCheck | null) => void }) {
  const router = useRouter();
  const client = useClaimClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const several = check.retakePhotos.length > 1;
  const allDone = check.retakePhotos.every((kind) => check.retaken.includes(kind));

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const sent = await client.sendRetakes();
      onChanged(await client.getClaim(), sent);
    } catch (caught) {
      setError(
        caught instanceof ClaimError && caught.code === "incomplete"
          ? `Take ${several ? "each photo" : "the photo"} again first, then send ${several ? "them" : "it"}.`
          : describeClaimError(caught),
      );
      setBusy(false);
    }
  };

  return (
    <Page
      title={several ? "We need new photos" : "We need a new photo"}
      intro={`Take ${several ? "them" : "it"} again and we will look once more. You do not need to start over.`}
      footer={
        <Button
          label={several ? "Send the new photos" : "Send the new photo"}
          onPress={send}
          loading={busy}
          disabled={!allDone}
          testID="claim-retake-send"
        />
      }
    >
      {check.clientMessage ? <Notice tone="warning">{check.clientMessage}</Notice> : null}
      {error ? <Notice tone="danger">{error}</Notice> : null}
      <Card>
        <View style={{ gap: 14 }}>
          {check.retakePhotos.map((kind) => (
            <PhotoRow
              key={kind}
              kind={kind}
              taken={check.retaken.includes(kind)}
              onPress={() => router.push(`/moving/photo/${kind}`)}
            />
          ))}
        </View>
      </Card>
    </Page>
  );
}

function Checking() {
  return (
    <Page
      title="We are checking it is you"
      intro="A person at Finvest is looking at what you sent. We will tell you as soon as it is done, and then you can check your investments."
    >
      <View style={{ alignItems: "center", marginVertical: 8 }}>
        <Badge icon="shield" />
      </View>
      <Steps now="identity" />
    </Page>
  );
}

/** Confirmed who they are: the investments carried over, to confirm or to say what is wrong. */
function Investments({ onChanged }: { onChanged: (claim: Claim | null, check: ClaimCheck | null) => void }) {
  const router = useRouter();
  const client = useClaimClient();
  const [busy, setBusy] = useState<"confirm" | "problem" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reporting, setReporting] = useState(false);
  const [description, setDescription] = useState("");

  const confirm = async () => {
    setBusy("confirm");
    setError(null);
    try {
      onChanged(await client.confirm(), null);
    } catch (caught) {
      setError(describeClaimError(caught));
      setBusy(null);
    }
  };

  const report = async () => {
    setBusy("problem");
    setError(null);
    try {
      onChanged(await client.reportProblem(description), null);
    } catch (caught) {
      setError(describeClaimError(caught));
      setBusy(null);
    }
  };

  if (reporting) {
    const text = description.trim();
    return (
      <Page
        title="What is wrong?"
        intro="Tell us what does not match what you had in Finvest 1.0, such as an amount, a date or an investment that is missing. Staff look into it and contact you."
        footer={
          <>
            <Button
              label="Send to Finvest"
              onPress={report}
              loading={busy === "problem"}
              disabled={!text || text.length > MAX_PROBLEM_LENGTH}
              testID="claim-problem-send"
            />
            <TextLink label="Back to my investments" onPress={() => setReporting(false)} />
          </>
        }
      >
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <TextField
          label="What you noticed"
          value={description}
          onChangeText={setDescription}
          multiline
          numberOfLines={5}
          maxLength={MAX_PROBLEM_LENGTH}
          hint={`${description.length} of ${MAX_PROBLEM_LENGTH} characters`}
          testID="claim-problem-input"
        />
      </Page>
    );
  }

  return (
    <Page
      title="Check your investments"
      intro="You are confirmed. These are the investments we carried over from Finvest 1.0. If they match what you had, confirm them."
      footer={
        <>
          <Button label="They are right" onPress={confirm} loading={busy === "confirm"} testID="claim-confirm" />
          <TextLink label="Something is wrong" onPress={() => setReporting(true)} testID="claim-problem" />
        </>
      }
    >
      <Steps now="investments" />
      {error ? <Notice tone="danger">{error}</Notice> : null}
      <Button
        label="See my investments"
        variant="secondary"
        onPress={() => router.push("/portfolio")}
        testID="claim-portfolio"
      />
      <Txt size={fontSizes.sm} color={colors.muted}>
        Look at the amount, start date and maturity date of each one. Confirming cannot be undone.
      </Txt>
    </Page>
  );
}

function Finished({ problem }: { problem: boolean }) {
  const router = useRouter();
  return (
    <Page
      title={problem ? "Thank you for telling us" : "You have moved to Finvest 2.0"}
      intro={
        problem
          ? "Staff are looking into what you told us and will contact you. You can keep using your account meanwhile."
          : "Your investments are confirmed. Everything you do from now on happens here, in Finvest 2.0."
      }
      footer={<Button label="Go to Home" onPress={() => router.replace(HOME)} testID="claim-home" />}
    >
      <View style={{ alignItems: "center", marginVertical: 8 }}>
        <Badge icon={problem ? "shield" : "check"} />
      </View>
      <Steps now="investments" done={!problem} />
    </Page>
  );
}

/** Moving from Finvest 1.0: where the claim stands, and the next thing to do. Home links here. */
export default function Moving() {
  const state = useClaim();

  if (state.status !== "ready") {
    return (
      <Screen signedIn>
        <Stack.Screen options={{ title: TITLE }} />
        <BackButton fallback={HOME} />
        <View style={{ marginTop: 28, gap: 16 }}>
          <Title>{TITLE}</Title>
          {state.status === "loading" ? (
            <Loading label="Loading your move" />
          ) : (
            <LoadFailed message={state.message} onRetry={state.reload} />
          )}
        </View>
      </Screen>
    );
  }

  const { claim, check } = state;
  if (!claim) return <Redirect href={HOME} />;
  switch (claim.status) {
    case "signed_in":
      return claim.identityStep === "selfie" ? <Selfie check={check} onChanged={state.replace} /> : <FullCheck />;
    case "identity_submitted":
      return check?.status === "awaiting_client" ? <Retake check={check} onChanged={state.replace} /> : <Checking />;
    case "identity_confirmed":
      return <Investments onChanged={state.replace} />;
    case "confirmed":
      return <Finished problem={false} />;
    case "problem_reported":
      return <Finished problem />;
    default:
      return <Redirect href={HOME} />;
  }
}
