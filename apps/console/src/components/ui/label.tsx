import type { ComponentProps } from "react";
import { cn } from "@/lib.ts";

export function Label({ className, ...props }: ComponentProps<"label">) {
  return <label className={cn("text-sm font-bold text-ink", className)} {...props} />;
}
