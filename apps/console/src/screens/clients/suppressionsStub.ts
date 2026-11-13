/**
 * Sample email suppressions for `pnpm dev` and preview builds. The hashes are made up, not the
 * real HMAC the API uses. The rules follow the API: two people, and a complaint is never lifted.
 */

import { SuppressionsError, type Lift, type Suppression, type SuppressionsApi } from "./suppressions.ts";

interface Me {
  id: string;
  name: string;
}

/** A stand-in for the API's address hash: stable, 64 hex characters, not secret. */
export function stubHash(email: string): string {
  const text = email.trim().toLowerCase();
  let out = "";
  for (let round = 0; round < 8; round++) {
    let hash = 0x811c9dc5 ^ round;
    for (const char of text) hash = Math.imul(hash ^ char.charCodeAt(0), 0x01000193) >>> 0;
    out += hash.toString(16).padStart(8, "0");
  }
  return out;
}

const wait = () => new Promise((resolve) => setTimeout(resolve, 150));
const copy = <T>(value: T): T => structuredClone(value);

function samples(now: number): Suppression[] {
  const ago = (hours: number) => new Date(now - hours * 60 * 60 * 1000).toISOString();
  const sarah = stubHash("sarah.n@example.com");
  return [
    {
      addressHash: sarah,
      reason: "bounced",
      createdAt: ago(30),
      lastEventAt: ago(30),
      suppressed: true,
      liftedAt: null,
      complained: false,
      openLift: {
        id: "-NstubLift0000000001",
        addressHash: sarah,
        reason: "Client says their mailbox was full and they have cleared it.",
        proposedBy: "sample-grace",
        proposedByName: "Grace Auma",
        proposedAt: ago(2),
        status: "proposed",
        decidedAt: null,
        decisionReason: null,
        yours: false,
      },
    },
    {
      addressHash: stubHash("j.okot@example.com"),
      reason: "complained",
      createdAt: ago(200),
      lastEventAt: ago(200),
      suppressed: true,
      liftedAt: null,
      complained: true,
      openLift: null,
    },
    {
      addressHash: stubHash("old.address@example.com"),
      reason: "bounced",
      createdAt: ago(400),
      lastEventAt: ago(400),
      suppressed: true,
      liftedAt: null,
      complained: false,
      openLift: null,
    },
    {
      addressHash: stubHash("fixed.inbox@example.com"),
      reason: "bounced",
      createdAt: ago(600),
      lastEventAt: ago(600),
      suppressed: false,
      liftedAt: ago(500),
      complained: false,
      openLift: null,
    },
  ];
}

export function createStubSuppressionsApi(me: Me, now: () => number = Date.now, canLift = true): SuppressionsApi {
  const all = samples(now());
  let next = 10;
  const allowed = () => {
    if (!canLift) throw new SuppressionsError("forbidden");
  };
  return {
    async list() {
      await wait();
      return { suppressions: copy(all), canLift };
    },
    async lookup(email) {
      await wait();
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) throw new SuppressionsError("invalid_email");
      const found = all.find((item) => item.addressHash === stubHash(email));
      return found ? copy(found) : null;
    },
    async propose(addressHash, reason) {
      await wait();
      allowed();
      const item = all.find((entry) => entry.addressHash === addressHash);
      if (!item) throw new SuppressionsError("not_found");
      if (!reason.trim()) throw new SuppressionsError("invalid");
      if (item.complained) throw new SuppressionsError("complained");
      if (!item.suppressed) throw new SuppressionsError("not_suppressed");
      if (item.openLift) throw new SuppressionsError("waiting");
      const lift: Lift = {
        id: `-NstubLift00000000${String(next++).padStart(2, "0")}`,
        addressHash,
        reason: reason.trim(),
        proposedBy: me.id,
        proposedByName: me.name,
        proposedAt: new Date(now()).toISOString(),
        status: "proposed",
        decidedAt: null,
        decisionReason: null,
        yours: true,
      };
      item.openLift = lift;
      return copy(lift);
    },
    async decide(liftId, decision, reason) {
      await wait();
      allowed();
      const item = all.find((entry) => entry.openLift?.id === liftId);
      const lift = item?.openLift;
      if (!item || !lift) throw new SuppressionsError("not_found");
      if (!reason.trim()) throw new SuppressionsError("invalid");
      if (lift.yours !== (decision === "withdrawn")) throw new SuppressionsError("same_person");
      const decided: Lift = { ...lift, status: decision, decidedAt: new Date(now()).toISOString(), decisionReason: reason.trim() };
      item.openLift = null;
      if (decision === "approved") {
        item.suppressed = false;
        item.liftedAt = decided.decidedAt;
      }
      return copy(decided);
    },
  };
}
