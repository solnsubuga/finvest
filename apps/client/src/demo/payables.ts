/**
 * What the demo payments and demo investing share, standing in for the
 * backend, where payments asks investments what is payable and investments
 * hears when a payment is confirmed. Only the in-memory stubs import this, so
 * it never reaches a production build.
 */
import type { Payable } from "@/payments/types";

const payables = new Map<string, Payable>();
const confirmed = new Map<string, string>();

/** Something the demo investing has saved, waiting to be paid for. */
export function addDemoPayable(payable: Payable): void {
  payables.set(payable.id, payable);
}

export function demoPayable(payableId: string): Payable | null {
  return payables.get(payableId) ?? null;
}

/** The demo payments calls this once a payment for the payable is confirmed. */
export function markDemoPaid(payableId: string, confirmedAt: string): void {
  if (!confirmed.has(payableId)) {
    confirmed.set(payableId, confirmedAt);
  }
}

/** When the payable was paid for, or null. */
export function demoPaidAt(payableId: string): string | null {
  return confirmed.get(payableId) ?? null;
}
