import type { ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";

export type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

export type Tone = "waiting" | "done" | "refused" | "quiet";

const TONES: Record<Tone, string> = {
  waiting: "bg-warning-surface text-warning-text",
  done: "bg-success-surface text-success",
  refused: "bg-danger-surface text-danger",
  quiet: "bg-surface-sunken text-muted",
};

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold", TONES[tone])}>{children}</span>;
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="min-w-0 flex-[1_1_260px]">
      <h3 className="m-0 text-xs font-bold tracking-[0.6px] text-muted uppercase">{title}</h3>
      <dl className="m-0">{children}</dl>
    </section>
  );
}

export function Row({ name, value }: { name: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-divider py-2.5 text-md">
      <dt className="shrink-0 text-muted">{name}</dt>
      <dd className="m-0 min-w-0 text-right font-bold break-words">{value}</dd>
    </div>
  );
}

export function FieldNote({ id, error, children }: { id: string; error?: string | undefined; children: ReactNode }) {
  return (
    <p id={id} className={cn("m-0 text-xs", error ? "font-semibold text-danger" : "text-muted")}>
      {error ?? children}
    </p>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
      {children}
    </p>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="m-0 rounded-md bg-danger-surface px-3.5 py-3 text-md font-semibold text-danger">
      {children}
    </p>
  );
}

export function Panel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <article aria-label={label} className="flex min-w-0 flex-col gap-4 rounded-xl border border-border bg-surface p-5">
      {children}
    </article>
  );
}

export function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
      <h2 className="m-0 text-xl font-extrabold">{title}</h2>
      <p className="m-0 text-md leading-normal text-muted">{children}</p>
    </section>
  );
}

export function Failure({ title, message, onRetry }: { title: string; message: string; onRetry?: () => void }) {
  return (
    <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
      <h2 className="m-0 text-xl font-extrabold">{title}</h2>
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </section>
  );
}

export function Loading({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
      {children}
    </p>
  );
}

/** A list entry that opens an item on the right, as on the identity checks queue. */
export function QueueButton({ selected, onSelect, children }: { selected: boolean; onSelect: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-current={selected ? "true" : undefined}
      onClick={onSelect}
      className={cn(
        "flex w-full cursor-pointer flex-col rounded-lg border bg-surface px-4 py-3.5 text-left text-ink hover:bg-surface-sunken",
        selected ? "border-2 border-black px-[15px] py-[13px]" : "border-border",
      )}
    >
      {children}
    </button>
  );
}

export const FIELD = "h-11 rounded-md text-md font-medium";
export const SELECT = "h-11 rounded-md border border-border-strong bg-surface px-3 text-md font-medium";
