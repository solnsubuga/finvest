import { useCallback, useState } from "react";
import { useFocusEffect } from "expo-router";

import { useIdentity } from "./IdentityProvider";
import { describeIdentityError, IdentityCheck } from "./types";

export type CheckState =
  | { status: "loading" }
  | { status: "ready"; check: IdentityCheck | null }
  | { status: "error"; message: string };

/**
 * The client's identity check. Read again every time the screen comes back
 * into view, so it is right after a step or a decision by staff.
 */
export function useIdentityCheck(): CheckState & { reload: () => void; replace: (check: IdentityCheck) => void } {
  const { client } = useIdentity();
  const [state, setState] = useState<CheckState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useFocusEffect(
    useCallback(() => {
      let current = true;
      client.getCheck().then(
        (check) => current && setState({ status: "ready", check }),
        (caught: unknown) => current && setState({ status: "error", message: describeIdentityError(caught) }),
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

  const replace = useCallback((check: IdentityCheck) => setState({ status: "ready", check }), []);

  return { ...state, reload, replace };
}
