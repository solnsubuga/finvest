import { useCallback, useState } from "react";
import { useFocusEffect } from "expo-router";

import { useClaimClient } from "./ClaimProvider";
import { Claim, describeClaimError } from "./types";

export type ClaimState =
  | { status: "loading" }
  | { status: "ready"; claim: Claim | null }
  | { status: "error"; message: string };

/**
 * The signed-in client's claim. Read again every time the screen comes back
 * into view, so it is right after the ID check or a decision by staff.
 */
export function useClaim(): ClaimState & {
  reload: () => void;
  replace: (claim: Claim | null) => void;
} {
  const client = useClaimClient();
  const [state, setState] = useState<ClaimState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useFocusEffect(
    useCallback(() => {
      let current = true;
      client.getClaim().then(
        (claim) => current && setState({ status: "ready", claim }),
        (caught: unknown) => current && setState({ status: "error", message: describeClaimError(caught) }),
      );
      return () => {
        current = false;
      };
      // `attempt` is here so "Try again" asks once more.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [client, attempt]),
  );

  const reload = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((value) => value + 1);
  }, []);

  const replace = useCallback(
    (claim: Claim | null) => setState({ status: "ready", claim }),
    [],
  );

  return { ...state, reload, replace };
}
