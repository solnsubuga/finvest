import type { NavItem } from "@/nav/nav.ts";
import { PageHeader } from "./PageHeader.tsx";

const PHASE_NAMES: Record<NavItem["phase"], string> = {
  0: "Foundations",
  1: "Money in and out",
  2: "Investing",
  3: "Migration and launch",
  4: "Goals and growth",
};

/** Stands in for a screen that is on the canvas but not built yet. */
export function PlaceholderPage({ item }: { item: NavItem }) {
  return (
    <>
      <PageHeader title={item.title} summary={item.summary} />
      <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
        <span className="self-start rounded-pill bg-warning-surface px-2.5 py-0.5 text-xs font-bold text-warning-text">
          Not built yet
        </span>
        <h2 className="m-0 text-xl font-extrabold">This screen is on the way</h2>
        <p className="m-0 text-md leading-normal text-muted">
          It ships with phase {item.phase} of the build, {PHASE_NAMES[item.phase]}. Nothing here reads or changes
          client data yet.
        </p>
      </section>
    </>
  );
}
