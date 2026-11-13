import { cn } from "@/lib.ts";
import type { QueueItem } from "./api.ts";
import { DOCUMENT_LABELS, STATUS_LABELS, timeAgo } from "./labels.ts";

interface QueueProps {
  items: readonly QueueItem[];
  selectedId: string | null;
  now: Date;
  onSelect: (id: string) => void;
}

/** The checks that are not decided yet. The ones needing a person come first. */
export function Queue({ items, selectedId, now, onSelect }: QueueProps) {
  return (
    <ul aria-label="Identity checks" className="m-0 flex list-none flex-col gap-2.5 p-0">
      {items.map((item) => {
        const selected = item.id === selectedId;
        const waiting = item.status === "needs_review";
        return (
          <li key={item.id}>
            <button
              type="button"
              aria-current={selected ? "true" : undefined}
              onClick={() => onSelect(item.id)}
              className={cn(
                "flex w-full cursor-pointer flex-col rounded-lg border bg-surface px-4 py-3.5 text-left text-ink hover:bg-surface-sunken",
                selected ? "border-2 border-black px-[15px] py-[13px]" : "border-border",
              )}
            >
              <span className="text-md font-bold">{item.name ?? "Name not confirmed"}</span>
              <span className="text-xs text-muted">
                {DOCUMENT_LABELS[item.documentType]}, submitted {timeAgo(item.submittedAt, now)}
              </span>
              <span className={cn("mt-1 text-xs font-bold", waiting ? "text-warning-text" : "text-muted")}>
                {STATUS_LABELS[item.status]}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
