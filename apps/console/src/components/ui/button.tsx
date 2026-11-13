import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib.ts";

const buttonVariants = cva(
  "inline-flex cursor-pointer items-center justify-center gap-2 font-bold transition-colors disabled:cursor-not-allowed disabled:opacity-60",
  {
    variants: {
      variant: {
        primary: "bg-black text-white hover:bg-ink",
        secondary: "border border-border-strong bg-surface text-ink hover:bg-surface-sunken",
        accent: "bg-lime text-ink",
        onBlack: "border border-on-black-border text-white hover:bg-on-black-surface",
      },
      size: {
        md: "h-11 rounded-pill px-4 text-sm",
        lg: "h-14 rounded-pill px-6 text-lg",
        block: "h-11 rounded-md px-4 text-sm",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

type ButtonProps = ComponentProps<"button"> & VariantProps<typeof buttonVariants>;

export function Button({ className, variant, size, type = "button", ...props }: ButtonProps) {
  return <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
