/** Sample movements for `pnpm dev` and preview builds. Nothing here is real. */
import { MovementsError, type Movement, type MovementFilter, type MovementsApi } from "./api.ts";

const PAGE = 6;

function samples(now: number): Movement[] {
  const ago = (minutes: number) => new Date(now - minutes * 60_000).toISOString();
  const row = (n: number, extra: Partial<Movement> & Pick<Movement, "kind" | "channel" | "status">): Movement => ({
    id: `-NstubMove0000000${String(n).padStart(3, "0")}`,
    clientId: "-NstubClient00000042",
    clientName: "Sarah Nakato",
    amount: { minor: 500_000, currency: "UGX" },
    failureCode: null,
    createdAt: ago(n * 37),
    endedAt: null,
    ...extra,
  });
  return [
    row(1, { kind: "deposit", channel: "mtn", status: "confirmed", amount: { minor: 2_500_000, currency: "UGX" }, endedAt: ago(36) }),
    row(2, { kind: "payout", channel: "mobile_money", status: "sent", clientId: "-NstubClient00000017", clientName: "James Okot", amount: { minor: 1_000_000, currency: "UGX" } }),
    row(3, { kind: "deposit", channel: "bank_transfer", status: "in_review", clientId: "-NstubClient00000077", clientName: "Peter Mukasa", amount: { minor: 15_000_000, currency: "UGX" } }),
    row(4, { kind: "deposit", channel: "airtel", status: "failed", failureCode: "declined", clientId: "-NstubClient00000063", clientName: "Ruth Akello", amount: { minor: 200_000, currency: "UGX" }, endedAt: ago(140) }),
    row(5, { kind: "payout", channel: "bank", status: "paid", clientId: "-NstubClient00000058", clientName: "Annet Namuli", amount: { minor: 4_000_000, currency: "UGX" }, endedAt: ago(100) }),
    row(6, { kind: "deposit", channel: "mtn", status: "uncertain", clientId: "-NstubClient00000099", clientName: null, amount: { minor: 1_200_000, currency: "UGX" } }),
    row(7, { kind: "payout", channel: "mobile_money", status: "failed", failureCode: "account_not_found", amount: { minor: 300_000, currency: "UGX" }, endedAt: ago(250) }),
    row(8, { kind: "deposit", channel: "mtn", status: "confirmed", amount: { minor: 750_000, currency: "UGX" }, endedAt: ago(290) }),
  ];
}

const SHOWS: Record<MovementFilter, (movement: Movement) => boolean> = {
  all: () => true,
  deposits: (movement) => movement.kind === "deposit",
  payouts: (movement) => movement.kind === "payout",
  failed: (movement) => movement.status === "failed",
  unmatched: (movement) => movement.status === "in_review" || movement.status === "uncertain",
};

export function createStubMovementsApi(now: () => number = Date.now, delay = 150, sample: "sample" | "empty" | "error" = "sample"): MovementsApi {
  const all = sample === "empty" ? [] : samples(now());
  return {
    async list(show, after) {
      await new Promise((resolve) => setTimeout(resolve, delay));
      if (sample === "error") throw new MovementsError("unavailable");
      const found = all.filter(SHOWS[show]);
      const start = after ? found.findIndex((movement) => movement.id === after) + 1 : 0;
      const page = found.slice(start, start + PAGE);
      return { movements: structuredClone(page), nextAfter: start + PAGE < found.length ? (page.at(-1)?.id ?? null) : null };
    },
  };
}
