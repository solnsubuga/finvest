/**
 * What the app last heard from the API, for the offline banner and the maintenance screen.
 *
 * `http.ts` reports every answer: no answer at all is `offline`, the API's `maintenance` code
 * (or the edge's bare 502, 503 or 504 while the API is stopped) is `maintenance`, and anything
 * else the API answered is `online`. It is a hint for the screen only. A request that failed
 * this way is still uncertain where it says so, and nothing is retried because of it.
 */

export type Connection = "online" | "offline" | "maintenance";

type Listener = (connection: Connection) => void;

let current: Connection = "online";
const listeners = new Set<Listener>();

export function currentConnection(): Connection {
  return current;
}

export function reportConnection(next: Connection): void {
  if (next === current) return;
  current = next;
  for (const listener of listeners) listener(next);
}

export function onConnection(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Tests start each case online. */
export function resetConnection(): void {
  current = "online";
  listeners.clear();
}
