import { useFocusEffect } from "expo-router";
import { useCallback, useEffect, useState } from "react";

import { describeInvestingError } from "./types";

export type Loaded<T> =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; value: T };

/** Loads once on mount, with a retry for the error state. `describe` words a failure. */
export function useLoaded<T>(
  load: () => Promise<T>,
  describe: (error: unknown) => string = describeInvestingError,
): [Loaded<T>, () => void] {
  const [loaded, setLoaded] = useState<Loaded<T>>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    load().then(
      (value) => live && setLoaded({ state: "ready", value }),
      (error: unknown) => live && setLoaded({ state: "failed", message: describe(error) }),
    );
    return () => {
      live = false;
    };
    // `describe` is a plain function that words an error; a new one does not need a new read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, attempt]);
  const retry = useCallback(() => {
    setLoaded({ state: "loading" });
    setAttempt((count) => count + 1);
  }, []);
  return [loaded, retry];
}

/** Like `useLoaded`, but reads again whenever the screen comes back into view. */
export function useFocusLoaded<T>(
  load: () => Promise<T>,
  describe: (error: unknown) => string = describeInvestingError,
): [Loaded<T>, () => void] {
  const [loaded, setLoaded] = useState<Loaded<T>>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useFocusEffect(
    useCallback(() => {
      let live = true;
      load().then(
        (value) => live && setLoaded({ state: "ready", value }),
        (error: unknown) => live && setLoaded({ state: "failed", message: describe(error) }),
      );
      return () => {
        live = false;
      };
      // `attempt` is here so "Try again" reads once more.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [load, attempt]),
  );
  const retry = useCallback(() => {
    setLoaded({ state: "loading" });
    setAttempt((count) => count + 1);
  }, []);
  return [loaded, retry];
}
