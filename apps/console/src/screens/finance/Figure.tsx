import { cn } from "@/lib.ts";

/** One figure in a row of them, inside a `<dl>`. */
export function Figure({ name, value, note, warn = false }: { name: string; value: string; note: string; warn?: boolean }) {
  return (
    <div className={cn("rounded-[18px] bg-surface p-4", warn ? "border-2 border-warning p-[15px]" : "border border-border")}>
      <dt className="text-sm text-muted">{name}</dt>
      <dd className="m-0 text-[22px] font-extrabold tracking-[-0.4px] tabular-nums">{value}</dd>
      <dd className={cn("m-0 text-xs font-bold", warn ? "text-warning-text" : "text-muted")}>{note}</dd>
    </div>
  );
}
