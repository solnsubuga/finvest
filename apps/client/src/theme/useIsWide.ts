import { useWindowDimensions } from "react-native";

import { TWO_COLUMN_BREAKPOINT, WIDE_BREAKPOINT } from "./index";

export function useIsWide(): boolean {
  const { width } = useWindowDimensions();
  return width >= WIDE_BREAKPOINT;
}

/** True when a wide screen has room for two columns of content beside the sidebar. */
export function useHasTwoColumns(): boolean {
  const { width } = useWindowDimensions();
  return width >= TWO_COLUMN_BREAKPOINT;
}
