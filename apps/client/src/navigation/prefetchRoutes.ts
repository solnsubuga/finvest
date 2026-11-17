/**
 * On a phone every screen is in the app already, so there is nothing to load ahead.
 * The web version is in prefetchRoutes.web.ts.
 */
export function prefetchRoutes(): Promise<void> {
  return Promise.resolve();
}
