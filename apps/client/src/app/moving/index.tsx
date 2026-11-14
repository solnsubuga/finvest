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
import { Claim, describeClaimError, MAX_PROBLEM_LENGTH } from "@/claim/types";
import { useClaim } from "@/claim/useClaim";
import { Badge, Progress, StatusLine } from "@/identity/Parts";
import { colors, fontSizes } from "@/theme";

const HOME = "/";
const TITLE = "Opening your account";

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
          detail="The ones we copied from your paper records"
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

/** Every client moved from paper records takes the full ID check. */
function FullCheck() {
  const router = useRouter();
  return (
    <Page
      title="Confirm who you are"
      intro="Before you can withdraw or invest, we check your ID, as we do for everyone who joins. It takes about two minutes: photos of your ID, a selfie and your details."
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
function Investments({ onChanged }: { onChanged: (claim: Claim | null) => void }) {
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
      onChanged(await client.confirm());
    } catch (caught) {
      setError(describeClaimError(caught));
      setBusy(null);
    }
  };

  const report = async () => {
    setBusy("problem");
    setError(null);
    try {
      onChanged(await client.reportProblem(description));
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
        intro="Tell us what does not match what you had with Finvest, such as an amount, a date or an investment that is missing. Staff look into it and contact you."
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
      intro="You are confirmed. These are the investments we copied from your paper records. If they match what you had, confirm them."
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
      title={problem ? "Thank you for telling us" : "Your account is ready"}
      intro={
        problem
          ? "Staff are looking into what you told us and will contact you. You can keep using your account meanwhile."
          : "Your investments are confirmed. From now on you can follow and manage them here, in the Finvest app."
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

/** Opening an account moved from paper records: where it stands, and what is next. Home links here. */
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

  const { claim } = state;
  if (!claim) return <Redirect href={HOME} />;
  switch (claim.status) {
    case "signed_in":
      return <FullCheck />;
    case "identity_submitted":
      return <Checking />;
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
