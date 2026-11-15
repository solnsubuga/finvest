/**
 * On a phone every screen's code is inside the app, so there is nothing to fetch again: the
 * boundary's own retry draws the screen once more.
 */
export function canReloadPage(): boolean {
  return false;
}

export function reloadPage(): void {}

/** Calls `back` when the device is online again. Phones have no such event here. */
export function onBackOnline(_back: () => void): () => void {
  return () => undefined;
}

export function isOffline(): boolean {
  return false;
}
