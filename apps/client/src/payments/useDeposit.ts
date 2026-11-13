import { useEffect, useState } from "react";
import { AppState } from "react-native";

import { usePayments } from "./PaymentsProvider";
import { POLL_MS, SLOW_AFTER_MS, SLOW_POLL_MS } from "./pollInterval";
import { Deposit, isFinal, PaymentError } from "./types";

export type DepositView = {
  deposit: Deposit | null;
  /** True when no such payment exists for this client. */
  missing: boolean;
  /** True when the latest read failed. The last known state is still shown. */
  stale: boolean;
};

/**
 * Reads a payment and keeps reading it until it reaches a final state. The
 * reads slow down after the first minute and stop while the app is not in
 * front, then start again with a fresh read when it comes back.
 *
 * Reading is all this does. It never asks for the money again: a payment with
 * no clear answer is settled by the server asking the network, not by the app.
 */
export function useDeposit(depositId: string): DepositView {
  const { client } = usePayments();
  const [view, setView] = useState<DepositView>({ deposit: null, missing: false, stale: false });

  useEffect(() => {
    let live = true;
    let done = false;
    let reading = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedAt = Date.now();

    async function read() {
      if (reading) {
        return;
      }
      reading = true;
      try {
        const deposit = await client.getDeposit(depositId);
        if (!live) {
          return;
        }
        done = isFinal(deposit.status);
        setView({ deposit, missing: false, stale: false });
      } catch (error) {
        if (!live) {
          return;
        }
        if (error instanceof PaymentError && error.code === "not_found") {
          done = true;
          setView({ deposit: null, missing: true, stale: false });
        } else {
          setView((current) => ({ ...current, stale: true }));
        }
      } finally {
        reading = false;
      }
      // Only an app that says it is in the background is paused. Anything else keeps reading.
      if (live && !done && AppState.currentState !== "background") {
        const pace = Date.now() - startedAt < SLOW_AFTER_MS ? POLL_MS : SLOW_POLL_MS;
        timer = setTimeout(() => void read(), pace);
      }
    }

    const subscription = AppState.addEventListener("change", (state) => {
      clearTimeout(timer);
      if (state === "active" && !done) {
        void read();
      }
    });

    void read();
    return () => {
      live = false;
      subscription.remove();
      clearTimeout(timer);
    };
  }, [client, depositId]);

  return view;
}
