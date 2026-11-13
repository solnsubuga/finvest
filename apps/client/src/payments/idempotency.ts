import { randomUUID } from "expo-crypto";

/**
 * A key for one intent to pay. Make it once per visit to the pay screen and
 * reuse it for every try from that visit.
 */
export function newIdempotencyKey(): string {
  const key: unknown = randomUUID();
  // A missing key would make two different payments look like one request.
  if (typeof key !== "string" || key.length < 16) {
    throw new Error("Could not make an idempotency key");
  }
  return key;
}
