import { View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { RecoveryReason } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { LinkCard } from "@/components/LinkCard";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

/**
 * Locked out: the quickest way back in for what happened. A forgotten password and a proved
 * email have their own flows; a lost phone or missing codes need a call from Finvest, because
 * the number on the account is what proves it is the client.
 */
export default function GetBackIn() {
  const router = useRouter();
  const callBack = (reason: RecoveryReason) =>
    router.push({ pathname: "/recover/call-back", params: { reason } });

  return (
    <Screen>
      <Stack.Screen options={{ title: "Get back into your account" }} />
      <BackButton fallback="/login" />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>Get back into your account</Title>
        <Txt color={colors.muted}>
          Tell us what happened and we will show you the quickest way back in. Your money stays
          exactly where it is.
        </Txt>
      </View>

      <View style={{ marginTop: 20, gap: 10 }}>
        <LinkCard
          icon="key"
          title="I forgot my password"
          description="Set a new one with a code sent to your mobile number."
          onPress={() => router.push("/reset")}
          testID="recover-password"
        />
        <LinkCard
          icon="mail"
          title="I still have my email"
          description="Log in with an email you confirmed, and get your code by email."
          onPress={() => router.push({ pathname: "/login", params: { with: "email" } })}
          testID="recover-email"
        />
        <LinkCard
          icon="phone"
          title="I lost my phone or changed my number"
          description="We call you, check it is you, then move your account to your new number."
          onPress={() => callBack("lost_phone")}
          testID="recover-phone"
        />
        <LinkCard
          icon="shield"
          title="My codes do not arrive"
          description="We call you to check it is you and help you sign in another way."
          onPress={() => callBack("no_codes")}
          testID="recover-codes"
        />
      </View>

      <View style={{ marginTop: 20 }}>
        <Notice tone="neutral">
          Finvest will never ask for your password, your PIN or a code, on a call or anywhere else.
        </Notice>
      </View>
    </Screen>
  );
}
