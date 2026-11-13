import { View } from "react-native";

import { colors } from "@/theme";

import { Button } from "./Button";
import { Icon } from "./Icon";
import { Screen } from "./Screen";
import { Title, Txt } from "./Txt";

type Props = { title: string; detail: string; action: string; onPress: () => void };

/** The last screen of a set-up: what was turned on, and one way out. */
export function Done({ title, detail, action, onPress }: Props) {
  return (
    <Screen footer={<Button label={action} onPress={onPress} testID="done" />}>
      <View
        style={{
          marginTop: 70,
          width: 72,
          height: 72,
          borderRadius: 36,
          backgroundColor: colors.lime,
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Icon name="check" size={36} color={colors.black} strokeWidth={2.6} />
      </View>
      <View style={{ marginTop: 20, gap: 10 }}>
        <Title>{title}</Title>
        <Txt color={colors.muted}>{detail}</Txt>
      </View>
    </Screen>
  );
}
