import { ReactNode } from "react";
import { View } from "react-native";
import { Href } from "expo-router";

import { BackButton } from "./BackButton";
import { Txt } from "./Txt";

type Props = { title: string; fallback: Href; action?: ReactNode };

/** Back, the screen's name, and at most one control: the top row of a detail screen. */
export function StackHeader({ title, fallback, action }: Props) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
      <BackButton fallback={fallback} />
      <Txt accessibilityRole="header" size={20} weight="extrabold" style={{ flex: 1 }}>
        {title}
      </Txt>
      {action}
    </View>
  );
}
