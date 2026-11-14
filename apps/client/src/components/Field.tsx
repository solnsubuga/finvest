import { ReactNode, useState } from "react";
import { Platform, Pressable, StyleSheet, TextInput, TextInputProps, View } from "react-native";

import { COUNTRY_CODE, formatNational } from "@/auth/phone";
import { colors, fontFor, fontSizes, radii } from "@/theme";

import { Icon } from "./Icon";
import { Txt } from "./Txt";

type FieldProps = {
  label: string;
  hint?: string;
  error?: string | null;
  children: ReactNode;
};

/** Label above, hint or error below. The error replaces the hint. */
export function Field({ label, hint, error, children }: FieldProps) {
  return (
    <View style={styles.field}>
      <Txt size={fontSizes.sm} weight="bold">
        {label}
      </Txt>
      {children}
      {error ? (
        <Txt size={fontSizes.xs} weight="semibold" color={colors.danger} accessibilityLiveRegion="polite">
          {error}
        </Txt>
      ) : hint ? (
        <Txt size={fontSizes.xs} weight="medium" color={colors.muted}>
          {hint}
        </Txt>
      ) : null}
    </View>
  );
}

type InputProps = TextInputProps & { label: string; invalid?: boolean; trailing?: ReactNode };

/** The white rounded input. The border turns black when focused, red when invalid. */
export function Input({ label, invalid, trailing, style, onFocus, onBlur, ...rest }: InputProps) {
  const [focused, setFocused] = useState(false);
  return (
    <View style={styles.inputWrap}>
      <TextInput
        accessibilityLabel={label}
        placeholderTextColor={colors.muted}
        {...rest}
        onFocus={(event) => {
          setFocused(true);
          onFocus?.(event);
        }}
        onBlur={(event) => {
          setFocused(false);
          onBlur?.(event);
        }}
        style={[
          styles.input,
          webNoOutline,
          focused && styles.inputFocused,
          invalid && styles.inputInvalid,
          trailing ? { paddingRight: 52 } : null,
          style,
        ]}
      />
      {trailing ? <View style={styles.trailing}>{trailing}</View> : null}
    </View>
  );
}

type TextFieldProps = Omit<InputProps, "invalid"> & { hint?: string; error?: string | null };

export function TextField({ label, hint, error, ...rest }: TextFieldProps) {
  return (
    <Field label={label} hint={hint} error={error}>
      <Input label={label} invalid={Boolean(error)} {...rest} />
    </Field>
  );
}

type PhoneFieldProps = {
  value: string;
  onChangeText: (national: string) => void;
  hint?: string;
  error?: string | null;
  autoFocus?: boolean;
  label?: string;
  testID?: string;
};

/** Mobile number with the fixed +256 prefix. Digits are grouped as they are typed. */
export function PhoneField({
  value,
  onChangeText,
  hint,
  error,
  autoFocus,
  label = "Mobile number",
  testID = "phone-input",
}: PhoneFieldProps) {
  return (
    <Field label={label} hint={hint} error={error}>
      <View style={styles.phoneRow}>
        <View style={styles.prefix}>
          <Txt size={fontSizes.lg} weight="bold">
            {COUNTRY_CODE}
          </Txt>
        </View>
        <View style={{ flex: 1 }}>
          <Input
            label={label}
            invalid={Boolean(error)}
            value={formatNational(value)}
            onChangeText={(text) => onChangeText(formatNational(text))}
            keyboardType="phone-pad"
            inputMode="tel"
            autoComplete="tel-national"
            placeholder="772 123 456"
            autoFocus={autoFocus}
            maxLength={11}
            testID={testID}
          />
        </View>
      </View>
    </Field>
  );
}

type PasswordFieldProps = Omit<TextFieldProps, "secureTextEntry" | "trailing"> & { isNew?: boolean };

export function PasswordField({ isNew = false, ...rest }: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  return (
    <TextField
      {...rest}
      secureTextEntry={!visible}
      autoCapitalize="none"
      autoCorrect={false}
      autoComplete={isNew ? "new-password" : "current-password"}
      trailing={
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={visible ? "Hide password" : "Show password"}
          onPress={() => setVisible((current) => !current)}
          style={styles.eye}
        >
          <Icon name={visible ? "eyeOff" : "eye"} color={colors.muted} />
        </Pressable>
      }
    />
  );
}

// React Native Web draws its own focus ring. The border already shows focus.
const webNoOutline = Platform.OS === "web" ? ({ outlineStyle: "none" } as object) : null;

const styles = StyleSheet.create({
  field: { gap: 6 },
  inputWrap: { position: "relative" },
  input: {
    height: 54,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    paddingHorizontal: 14,
    fontFamily: fontFor("semibold"),
    fontSize: fontSizes.lg,
    color: colors.ink,
  },
  inputFocused: { borderWidth: 2, borderColor: colors.black, paddingHorizontal: 13 },
  inputInvalid: { borderWidth: 2, borderColor: colors.danger, paddingHorizontal: 13 },
  trailing: { position: "absolute", right: 4, top: 5 },
  eye: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  phoneRow: { flexDirection: "row", gap: 8 },
  prefix: {
    height: 54,
    paddingHorizontal: 14,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    justifyContent: "center",
  },
});
