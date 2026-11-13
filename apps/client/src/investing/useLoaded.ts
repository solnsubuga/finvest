import { useCallback, useEffect, useState } from "react";

import { describeInvestingError } from "./types";

export type Loaded<T> =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; value: T };

/** Loads once on mount, with a retry for the error state. */
export function useLoaded<T>(load: () => Promise<T>): [Loaded<T>, () => void] {
  const [loaded, setLoaded] = useState<Loaded<T>>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    load().then(
      (value) => live && setLoaded({ state: "ready", value }),
      (error: unknown) => live && setLoaded({ state: "failed", message: describeInvestingError(error) }),
    );
    return () => {
      live = false;
    };
  }, [load, attempt]);
  const retry = useCallback(() => {
    setLoaded({ state: "loading" });
    setAttempt((count) => count + 1);
  }, []);
  return [loaded, retry];
}
