import { type ComponentType, lazy, type LazyExoticComponent } from "react";

/**
 * Finds the screens that have been built.
 *
 * A screen lives in its own folder, named after its path in src/nav/nav.ts,
 * with an index.tsx that default-exports the page:
 *
 *   src/screens/identity-checks/index.tsx
 *
 * That is all it takes to replace the placeholder. Nothing here or in the
 * routes needs editing, so two screens built at the same time do not collide.
 */
// Each screen is its own file, fetched when it is first opened, so signing in does not wait for
// the code of every screen (QA F-011: one 1.7 MB file before). routes.tsx shows a short wait.
const modules = import.meta.glob<{ default: ComponentType }>("./*/index.tsx");

export type Screen = LazyExoticComponent<ComponentType>;

export function screensFrom(
  found: Record<string, () => Promise<{ default: ComponentType }>>,
): ReadonlyMap<string, Screen> {
  const screens = new Map<string, Screen>();
  for (const [file, load] of Object.entries(found)) {
    const path = /^\.\/([^/]+)\/index\.tsx$/.exec(file)?.[1];
    if (path) screens.set(path, lazy(load));
  }
  return screens;
}

export const SCREENS = screensFrom(modules);
