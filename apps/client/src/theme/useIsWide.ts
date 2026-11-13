import { useWindowDimensions } from "react-native";

import { WIDE_BREAKPOINT } from "./index";

export function useIsWide(): boolean {
  const { width } = useWindowDimensions();
  return width >= WIDE_BREAKPOINT;
}
