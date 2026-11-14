import { api } from "@/api/http";

import type { Bank } from "./types";

/**
 * The banks a client can be paid to, as the backend lists them (`GET /client/banks`). The
 * list changes only with a release, so it is read once and shared by every screen that names
 * a bank. A failed read is not kept, so the next screen tries again.
 */
let read: Promise<Bank[]> | null = null;

export function readBanks(): Promise<Bank[]> {
  if (!read) {
    read = api("GET /client/banks", {}).then(
      (views) => views.map((view) => ({ code: view.code, name: view.name })),
      (error: unknown) => {
        read = null;
        throw error;
      },
    );
  }
  return read;
}

/** The bank names, or none when the list cannot be read: an account still shows, as "Bank". */
export async function banksOrNone(): Promise<Bank[]> {
  try {
    return await readBanks();
  } catch {
    return [];
  }
}

/** Tests start from an empty cache. */
export function forgetBanks(): void {
  read = null;
}

/** The bank's name for a code, or null when the list does not hold it. */
export function nameOf(banks: Bank[], code: string | null): string | null {
  return banks.find((bank) => bank.code === code)?.name ?? null;
}
