import type { ComponentType } from "react";

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
const modules = import.meta.glob<{ default: ComponentType }>("./*/index.tsx", { eager: true });

export function screensFrom(
  found: Record<string, { default: ComponentType }>,
): ReadonlyMap<string, ComponentType> {
  const screens = new Map<string, ComponentType>();
  for (const [file, module] of Object.entries(found)) {
    const path = /^\.\/([^/]+)\/index\.tsx$/.exec(file)?.[1];
    if (path) screens.set(path, module.default);
  }
  return screens;
}

export const SCREENS = screensFrom(modules);
