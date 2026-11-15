import { View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { StatusLine } from "@/identity/Parts";
import { colors } from "@/theme";

/**
 * "Open an account for a company or group", from the sign-up screen. The person signing up is the
 * applicant and signs for it, so they open their own account and pass the ID check first. The
 * company or group application follows from Home once that check is approved.
 */
export default function SignupOrganisation() {
  const router = useRouter();
  const { setReturnTo } = useAuth();

  return (
    <Screen
      footer={
        <>
          <Button
            label="Create my own account first"
            onPress={() => router.push({ pathname: "/signup", params: { for: "organisation" } })}
            testID="organisation-signup-start"
          />
          <TextLink
            label="I already have a Finvest account"
            onPress={() => {
              // Straight to the application after logging in.
              setReturnTo("/account/organisations/new");
              router.push("/login");
            }}
          />
        </>
      }
    >
      <Stack.Screen options={{ title: "Company and group accounts" }} />
      <BackButton fallback="/signup" />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Eyebrow>Company or group</Eyebrow>
        <Title>It starts with your own account</Title>
        <Txt color={colors.muted}>
          You apply for the company, SACCO or group and sign for it, so we check who you are first. It takes three
          steps.
        </Txt>
      </View>

      <View style={{ marginTop: 22, gap: 14 }}>
        <Card>
          <View style={{ gap: 16 }}>
            <StatusLine state="now" title="Create your own account" detail="Your phone number, email and a password" />
            <StatusLine state="next" title="Verify your identity" detail="Your National ID or passport and a selfie" />
            <StatusLine
              state="next"
              title="Apply for the company or group"
              detail="Its registration papers and who signs for it"
            />
          </View>
        </Card>
        <Notice tone="neutral">
          Everyone who signs for the account needs an approved Finvest account of their own. A person at Finvest checks
          the application, usually within 2 working days.
        </Notice>
      </View>
    </Screen>
  );
}
