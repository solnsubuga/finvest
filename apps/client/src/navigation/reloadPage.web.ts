/**
 * The web build fetches a screen's code the first time it opens. A fetch that failed is
 * remembered for the rest of the visit, so the screen can only come back by loading the page
 * again. The address stays the same, so the client lands on the screen they asked for, and the
 * session is kept in its cookie.
 */
export function canReloadPage(): boolean {
  return typeof window !== "undefined";
}

export function reloadPage(): void {
  window.location.reload();
}

/** Calls `back` once when the browser says it is online again. Returns the way to stop. */
export function onBackOnline(back: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const listener = () => back();
  window.addEventListener("online", listener, { once: true });
  return () => window.removeEventListener("online", listener);
}

export function isOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}
