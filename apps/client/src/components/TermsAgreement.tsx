import { Linking, Pressable, StyleSheet, View } from "react-native";

import { TextLink } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

/** The website's legal pages. The API records the version current when the box is ticked. */
const SITE = "https://finvestug.com";

const ERROR_ID = "terms-error";

/**
 * "I agree to the terms and the privacy notice": the tick box at sign-up and when claiming an
 * account moved over from paper records, with links to read both first. The server records
 * which versions were agreed to, and Documents shows them under "Terms you accepted".
 */
export function TermsAgreement({
  agreed,
  onChange,
  invalid,
  adultLine = true,
}: {
  agreed: boolean;
  onChange: (agreed: boolean) => void;
  invalid: boolean;
  /** Sign-up also asks the client to confirm they are 18 or older. */
  adultLine?: boolean;
}) {
  return (
    <View style={{ gap: 2 }}>
      <Pressable
        accessibilityRole="checkbox"
        aria-checked={agreed}
        aria-invalid={invalid}
        aria-describedby={invalid ? ERROR_ID : undefined}
        onPress={() => onChange(!agreed)}
        style={styles.terms}
        testID="terms-checkbox"
      >
        <View
          style={[
            styles.box,
            agreed && styles.boxChecked,
            invalid && { borderColor: colors.danger },
          ]}
        >
          {agreed ? (
            <Icon name="check" size={16} color={colors.white} strokeWidth={3} />
          ) : null}
        </View>
        <Txt
          size={fontSizes.sm}
          style={{ flex: 1 }}
          color={invalid ? colors.danger : colors.ink}
        >
          {adultLine
            ? "I agree to the terms and the privacy notice, and I am 18 or older."
            : "I agree to the terms and the privacy notice."}
        </Txt>
      </Pressable>
      {invalid ? (
        // In words as well as red, and read out when it appears.
        <Txt
          nativeID={ERROR_ID}
          size={fontSizes.xs}
          weight="semibold"
          color={colors.danger}
          accessibilityLiveRegion="polite"
          style={{ paddingLeft: 34 }}
          testID="terms-error"
        >
          {adultLine
            ? "Tick the box to agree to the terms and confirm you are 18 or older."
            : "Tick the box to agree to the terms."}
        </Txt>
      ) : null}
      <View style={styles.links}>
        <TextLink
          label="Read the terms"
          onPress={() => void Linking.openURL(`${SITE}/legal/terms`)}
          testID="terms-link"
        />
        <TextLink
          label="Read the privacy notice"
          onPress={() => void Linking.openURL(`${SITE}/legal/privacy`)}
          testID="privacy-link"
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  terms: {
    flexDirection: "row",
    gap: 12,
    alignItems: "flex-start",
    minHeight: 44,
  },
  links: {
    flexDirection: "row",
    flexWrap: "wrap",
    columnGap: 20,
    paddingLeft: 34,
  },
  box: {
    width: 22,
    height: 22,
    borderRadius: 6,
    borderWidth: 2,
    borderColor: colors.black,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  boxChecked: { backgroundColor: colors.black },
});
