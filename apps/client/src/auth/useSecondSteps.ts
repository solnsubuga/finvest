import { useCallback, useState } from "react";
import { useFocusEffect } from "expo-router";

import { useAuth } from "./AuthProvider";
import { describeAuthError, SecondStep } from "./types";

type State =
  | { status: "loading" }
  | { status: "ready"; methods: SecondStep[] }
  | { status: "error"; message: string };

/**
 * The second steps the signed-in client has set up. Asked again every time
 * the screen comes back into view, so it is right after a set-up finishes.
 */
export function useSecondSteps(): State & { reload: () => void } {
  const { client } = useAuth();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useFocusEffect(
    useCallback(() => {
      let current = true;
      client.listSecondSteps().then(
        (methods) => {
          if (current) {
            setState({ status: "ready", methods });
          }
        },
        (caught: unknown) => {
          if (current) {
            setState({ status: "error", message: describeAuthError(caught) });
          }
        },
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

  return { ...state, reload };
}
