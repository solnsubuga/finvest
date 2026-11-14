import { useCallback, useState } from "react";
import { useFocusEffect } from "expo-router";

import { useClaimClient } from "./ClaimProvider";
import { Claim, ClaimCheck, describeClaimError } from "./types";

export type ClaimState =
  | { status: "loading" }
  | { status: "ready"; claim: Claim | null; check: ClaimCheck | null }
  | { status: "error"; message: string };

/**
 * The signed-in client's claim, and their selfie claim check when that is
 * their step. Read again every time the screen comes back into view, so it is
 * right after a photo or a decision by staff.
 */
export function useClaim(): ClaimState & {
  reload: () => void;
  replace: (claim: Claim | null, check: ClaimCheck | null) => void;
} {
  const client = useClaimClient();
  const [state, setState] = useState<ClaimState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useFocusEffect(
    useCallback(() => {
      let current = true;
      (async () => {
        const claim = await client.getClaim();
        // Read for a full ID check too: one sent there after photos that did not match keeps them.
        const check = claim ? await client.getCheck() : null;
        return { claim, check };
      })().then(
        ({ claim, check }) => current && setState({ status: "ready", claim, check }),
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
    (claim: Claim | null, check: ClaimCheck | null) => setState({ status: "ready", claim, check }),
    [],
  );

  return { ...state, reload, replace };
}
