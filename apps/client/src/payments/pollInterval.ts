/** How often the status is read while a payment is open, in milliseconds. */
export const POLL_MS = 3_000;

/** After the first minute most prompts are answered, so the reads slow down. */
export const SLOW_POLL_MS = 10_000;
export const SLOW_AFTER_MS = 60_000;
