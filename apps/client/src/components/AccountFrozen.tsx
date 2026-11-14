import { Linking, StyleSheet, View } from "react-native";
import { router } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { colors, fontSizes, radii } from "@/theme";

import { Button, TextLink } from "./Button";
import { Icon } from "./Icon";
import { Screen } from "./Screen";
import { Title, Txt } from "./Txt";

export const SUPPORT_EMAIL = "support@finvestug.com";

/**
 * After the client froze their account. The server has ended every session, so this shows with
 * no session at all and stays until the client leaves it. Unfreezing is two people at Finvest,
 * after the client gets in touch (docs/client-profile.md).
 */
export function AccountFrozen() {
  const { setFrozen } = useAuth();
  const done = () => {
    setFrozen(false);
    router.replace("/welcome");
  };
  return (
    <Screen
      dark
      footer={
        <>
          <Button
            label="Email Finvest"
            variant="lime"
            onPress={() => void Linking.openURL(`mailto:${SUPPORT_EMAIL}?subject=Unfreeze%20my%20account`)}
            testID="frozen-email"
          />
          <TextLink label="Done" onDark onPress={done} testID="frozen-done" />
        </>
      }
    >
      <View style={styles.center} testID="account-frozen">
        <View style={styles.badge}>
          <Icon name="lock" size={34} color={colors.black} />
        </View>
        <Title onDark center>
          Your account is frozen
        </Title>
        <Txt color={colors.onBlackMuted} center>
          We signed you out on every phone and browser. Nobody can log in, you included, until it is
          unfrozen. Your investments and automatic plans carry on as before.
        </Txt>
      </View>
      <View style={styles.box}>
        <Txt weight="bold" color={colors.white}>
          To unfreeze it
        </Txt>
        <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
          {`Email ${SUPPORT_EMAIL} from the address on your account, or find other ways to reach us at finvestug.com/help. Two people at Finvest check it is really you before your account is unfrozen.`}
        </Txt>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  center: { marginTop: 24, alignItems: "center", gap: 12 },
  badge: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 8,
  },
  box: {
    marginTop: 28,
    gap: 6,
    borderRadius: radii.md,
    paddingVertical: 14,
    paddingHorizontal: 16,
    backgroundColor: colors.onBlackSurface,
  },
});
