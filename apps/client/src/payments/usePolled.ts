import { useEffect, useState } from "react";
import { AppState } from "react-native";

import { POLL_MS, SLOW_AFTER_MS, SLOW_POLL_MS } from "./pollInterval";
import { PaymentError } from "./types";

export type Polled<T> = {
  value: T | null;
  /** True when no such thing exists for this client. */
  missing: boolean;
  /** True when the latest read failed. The last known state is still shown. */
  stale: boolean;
};

/**
 * Reads something and keeps reading it until `isDone` says it is final. The
 * reads slow down after the first minute and stop while the app is not in
 * front, then start again with a fresh read when it comes back.
 *
 * Reading is all this does. It never asks for money and never sends anything.
 * `read` and `isDone` must keep their identity between renders.
 */
export function usePolled<T>(read: () => Promise<T>, isDone: (value: T) => boolean): Polled<T> {
  const [view, setView] = useState<Polled<T>>({ value: null, missing: false, stale: false });

  useEffect(() => {
    let live = true;
    let done = false;
    let reading = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedAt = Date.now();

    async function tick() {
      if (reading) {
        return;
      }
      reading = true;
      try {
        const value = await read();
        if (!live) {
          return;
        }
        done = isDone(value);
        setView({ value, missing: false, stale: false });
      } catch (error) {
        if (!live) {
          return;
        }
        if (error instanceof PaymentError && error.code === "not_found") {
          done = true;
          setView({ value: null, missing: true, stale: false });
        } else {
          setView((current) => ({ ...current, stale: true }));
        }
      } finally {
        reading = false;
      }
      // Only an app that says it is in the background is paused. Anything else keeps reading.
      if (live && !done && AppState.currentState !== "background") {
        const pace = Date.now() - startedAt < SLOW_AFTER_MS ? POLL_MS : SLOW_POLL_MS;
        timer = setTimeout(() => void tick(), pace);
      }
    }

    const subscription = AppState.addEventListener("change", (state) => {
      clearTimeout(timer);
      if (state === "active" && !done) {
        void tick();
      }
    });

    void tick();
    return () => {
      live = false;
      subscription.remove();
      clearTimeout(timer);
    };
  }, [read, isDone]);

  return view;
}
