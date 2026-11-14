import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** "Grace Auma" becomes "GA". */
export function initials(name: string): string {
  return name
    .split(/\s+/)
    .map((part) => part.replace(/[^\p{L}]/gu, "").charAt(0))
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

function properWord(word: string): string {
  if (word !== word.toUpperCase() && word !== word.toLowerCase()) return word;
  return word
    .split(/([-'])/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join("");
}

/**
 * A name from an identity document as people read it: given names first, in proper case.
 * "NAKATO", "AMARA" becomes "Amara Nakato". A word that already mixes capitals ("McDonald") is
 * kept. The same rule as the API's (backend kyc/domain/names.py).
 */
export function shownName(surname: string, givenNames: string): string {
  return `${givenNames} ${surname}`.split(/\s+/).filter(Boolean).map(properWord).join(" ");
}
