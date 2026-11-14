import { cn } from "@/lib.ts";
import type { QueueItem } from "./api.ts";
import { STAGE_LABELS, formatDay, formatExact } from "./format.ts";

interface QueueProps {
  items: readonly QueueItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/** Transfers still being checked, newest first. */
export function Queue({ items, selectedId, onSelect }: QueueProps) {
  return (
    <ul aria-label="Bank transfers" className="m-0 flex list-none flex-col gap-2.5 p-0">
      {items.map((item) => {
        const selected = item.id === selectedId;
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
              <span className="text-md font-bold tabular-nums">{formatExact(item.amount)}</span>
              <span className="text-xs text-muted">
                {item.bankName}, sent {formatDay(item.sentOn)}
              </span>
              <span className={cn("mt-1 text-xs font-bold", item.stage === "to_match" || item.stage === "in_review" ? "text-warning-text" : "text-muted")}>
                {STAGE_LABELS[item.stage]}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
