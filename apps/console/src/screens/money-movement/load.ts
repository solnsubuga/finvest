import { useCallback, useEffect, useRef, useState } from "react";

export type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

/** Loads once (and again on `reload`), with errors worded by `message`. */
export function useLoad<T>(fetch: () => Promise<T>, message: (error: unknown) => string, enabled = true) {
  const [load, setLoad] = useState<Load<T>>({ state: "loading" });
  const run = useCallback(
    (): Promise<Load<T>> =>
      fetch().then(
        (value): Load<T> => ({ state: "ready", value }),
        (error: unknown): Load<T> => ({ state: "failed", message: message(error) }),
      ),
    [fetch, message],
  );
  useEffect(() => {
    if (!enabled) return;
    let current = true;
    void run().then((result) => current && setLoad(result));
    return () => {
      current = false;
    };
  }, [run, enabled]);
  const reload = useCallback(async () => setLoad(await run()), [run]);
  return [load, reload] as const;
}

/**
 * The idempotency key of a command not answered yet. Pressing it again sends the same key, so a
 * first try that did reach Finvest is replayed, not applied twice. `settle` closes it once the
 * answer is definite.
 */
export function useCommandKeys(newKey: () => string) {
  const pending = useRef<{ request: string; key: string } | null>(null);
  return {
    keyFor(request: string): string {
      if (pending.current?.request === request) return pending.current.key;
      const key = newKey();
      pending.current = { request, key };
      return key;
    },
    settle() {
      pending.current = null;
    },
  };
}
