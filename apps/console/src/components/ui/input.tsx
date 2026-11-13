import type { ComponentProps } from "react";
import { cn } from "@/lib.ts";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return (
    <input
      className={cn(
        "h-[54px] w-full rounded-lg border border-border-strong bg-surface px-3.5 text-lg font-semibold text-ink",
        "placeholder:font-medium placeholder:text-muted focus-visible:border-black focus-visible:outline-offset-0",
        "aria-invalid:border-danger",
        className,
      )}
      {...props}
    />
  );
}
