import { useEffect } from "react";
import { View } from "react-native";

import type { RegulaLivenessProps, RegulaOutcome } from "@/identity/regula/types";

/**
 * Stands in for Regula's liveness in tests, which have no camera or Regula service. A test ends
 * the session with `regulaSession.end(...)`, and reads what it was started with.
 */
let shown: RegulaLivenessProps | null = null;

export const regulaSession = {
  end(outcome: RegulaOutcome) {
    const props = shown;
    shown = null;
    props?.onEnd(outcome);
  },
  get open(): boolean {
    return shown !== null;
  },
  get tag(): string | null {
    return shown?.tag ?? null;
  },
  get serviceUrl(): string | null {
    return shown?.serviceUrl ?? null;
  },
};

export function RegulaLiveness(props: RegulaLivenessProps) {
  useEffect(() => {
    shown = props;
    return () => {
      if (shown === props) shown = null;
    };
  });
  return <View testID="regula-liveness" />;
}
