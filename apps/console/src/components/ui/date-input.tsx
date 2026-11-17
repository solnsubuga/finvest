import { type ComponentProps, useRef, useState } from "react";
import { cn } from "@/lib.ts";
import { INPUT_CLASSES } from "./input.tsx";

/**
 * A date field that reads and is typed as day/month/year, the way Uganda writes dates.
 *
 * A browser's own date field shows the order of the computer's language setting, so on a
 * laptop set to US English staff saw mm/dd/yyyy, and 03/04/2027 could be read either way. This
 * field is plain text in dd/mm/yyyy, with a calendar button that opens the browser's date picker.
 *
 * It takes and gives the same value as a browser date field: an ISO date (2027-04-03), or ""
 * while what is typed is not yet a whole real date. A pasted or typed ISO date is understood too.
 */
export function DateInput({
  value,
  onChange,
  min,
  max,
  className,
  plain = false,
  disabled,
  ...props
}: Omit<ComponentProps<"input">, "type" | "value" | "onChange" | "min" | "max"> & {
  value: string;
  onChange: (event: { target: { value: string } }) => void;
  min?: string | undefined;
  max?: string | undefined;
  /** For a field styled by its own class alone, as a plain `<input className={...}>` was. */
  plain?: boolean;
}) {
  const [text, setText] = useState(() => showDate(value));
  const [shown, setShown] = useState(value);
  const picker = useRef<HTMLInputElement>(null);

  // A new value from outside (a reset form, a picked date) replaces what is typed, unless what is
  // typed already means that date.
  if (value !== shown) {
    setShown(value);
    if (readDate(text) !== value) setText(showDate(value));
  }

  const give = (next: string) => {
    if (next !== value) onChange({ target: { value: next } });
  };

  return (
    <span className="flex min-w-0 items-stretch">
      <input
        {...props}
        type="text"
        inputMode="text"
        autoComplete="off"
        placeholder="dd/mm/yyyy"
        disabled={disabled}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          give(readDate(event.target.value));
        }}
        onBlur={(event) => {
          const iso = readDate(text);
          if (iso) setText(showDate(iso));
          props.onBlur?.(event);
        }}
        className={cn(plain ? null : INPUT_CLASSES, className, "pr-11 tabular-nums")}
      />
      <span className="relative -ml-11 flex w-11 shrink-0 items-center justify-center">
        <button
          type="button"
          disabled={disabled}
          aria-label="Choose the date from a calendar"
          title="Choose from a calendar"
          onClick={() => {
            const input = picker.current;
            if (!input) return;
            try {
              input.showPicker();
            } catch {
              input.focus();
            }
          }}
          className="flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-surface-sunken hover:text-ink disabled:opacity-40"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <rect x="3" y="5" width="18" height="16" rx="2" />
            <path d="M3 10h18M8 3v4M16 3v4" strokeLinecap="round" />
          </svg>
        </button>
        {/* The browser's own picker, out of sight: the button above opens it. */}
        <input
          ref={picker}
          type="date"
          tabIndex={-1}
          aria-hidden="true"
          data-picker=""
          value={value}
          min={min}
          max={max}
          disabled={disabled}
          onChange={(event) => {
            setText(showDate(event.target.value));
            give(event.target.value);
          }}
          className="pointer-events-none absolute right-0 bottom-0 h-px w-px opacity-0"
        />
      </span>
    </span>
  );
}

const DMY = /^(\d{1,2})[/.\-\s](\d{1,2})[/.\-\s](\d{4})$/;
const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

/** The ISO date that typed text means, or "" if it is not a whole real date. */
export function readDate(typed: string): string {
  const text = typed.trim();
  const iso = ISO.exec(text);
  const dmy = iso ? null : DMY.exec(text);
  const parts = iso ? [iso[1], iso[2], iso[3]] : dmy ? [dmy[3], dmy[2], dmy[1]] : null;
  if (!parts) return "";
  const [year, month, day] = parts.map(Number) as [number, number, number];
  if (year < 1900 || month < 1 || month > 12 || day < 1) return "";
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return "";
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/** An ISO date as dd/mm/yyyy, or "" for no date. */
export function showDate(iso: string): string {
  const parts = ISO.exec(iso);
  return parts ? `${parts[3]}/${parts[2]}/${parts[1]}` : "";
}
