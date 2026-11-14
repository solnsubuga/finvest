import { ActionError, LIMIT_KEYS, type ActionKind, type ActionsApi, type ClientAction, type Limits, type WithdrawalOptions } from "./actions.ts";

const SHARED: Limits = {
  mobileMoneyPerPayout: 5_000_000,
  mobileMoneyPerDay: 10_000_000,
  bankPerPayout: 50_000_000,
  bankPerDay: 100_000_000,
  reviewAbove: 20_000_000,
};

/** The lower of the two for each sort, as the backend applies them. */
function lower(shared: Limits, own: Limits | null): Limits {
  if (!own) return shared;
  const pick = (a: number | null, b: number | null) => (a === null ? b : b === null ? a : Math.min(a, b));
  return {
    mobileMoneyPerPayout: pick(shared.mobileMoneyPerPayout, own.mobileMoneyPerPayout),
    mobileMoneyPerDay: pick(shared.mobileMoneyPerDay, own.mobileMoneyPerDay),
    bankPerPayout: pick(shared.bankPerPayout, own.bankPerPayout),
    bankPerDay: pick(shared.bankPerDay, own.bankPerDay),
    reviewAbove: pick(shared.reviewAbove, own.reviewAbove),
  };
}

/**
 * Sample actions for `pnpm dev` and previews. For the first sample client Grace Auma has
 * proposed a PIN reset, so a reviewer can approve it, and a statement sent last week shows in
 * the history. `empty` starts with nothing proposed, no written request and early withdrawal
 * switched off.
 */
export function createStubActionsApi(
  me: { id: string; name: string },
  allowed: readonly ActionKind[],
  sample: "sample" | "empty" = "sample",
  now: () => number = Date.now,
  delayMs = 120,
): ActionsApi {
  const hour = 3_600_000;
  const ago = (hours: number) => new Date(now() - hours * hour).toISOString();
  const actions: (ClientAction & { clientId: string })[] = [];
  const own = new Map<string, Limits>();
  let next = 10;
  const base = {
    limits: null,
    statement: null,
    withdrawal: null,
    decidedByName: null,
    decidedAt: null,
    decisionReason: null,
  };
  if (sample === "sample") {
    actions.push(
      {
        ...base,
        id: "-NstubAction00000002",
        clientId: "-NstubClient00000042",
        kind: "pin_reset",
        reason: "Client called from their registered number. Forgot the app PIN after changing phones.",
        proposedByName: "Grace Auma",
        proposedAt: ago(1),
        status: "proposed",
        yours: false,
      },
      {
        ...base,
        id: "-NstubAction00000001",
        clientId: "-NstubClient00000042",
        kind: "statement",
        reason: "Client needs a statement for a bank loan application.",
        proposedByName: "Ruth Namata",
        proposedAt: ago(170),
        status: "approved",
        decidedByName: "Grace Auma",
        decidedAt: ago(168),
        decisionReason: "Checked the period with the client.",
        statement: { from: "2026-08-01", to: "2026-10-31" },
        yours: false,
      },
    );
  }
  const options: WithdrawalOptions =
    sample === "sample"
      ? {
          investments: [
            {
              id: "-NstubInvest00000011",
              productName: "182-day Treasury bill",
              valueMinor: 2_084_500,
              currency: "UGX",
              maturityDate: "2027-02-04",
            },
            {
              id: "-NstubInvest00000012",
              productName: "Fixed income, 12 months",
              valueMinor: 5_310_000,
              currency: "UGX",
              maturityDate: "2027-08-03",
            },
          ],
          accounts: [
            {
              id: "-NstubDest0000000001",
              kind: "mobile_money",
              network: "MTN",
              bankCode: null,
              maskedAccount: "••• 456",
              holderName: "Sarah Nakato",
            },
            {
              id: "-NstubDest0000000002",
              kind: "bank",
              network: null,
              bankCode: "STANBIC",
              maskedAccount: "•••• 0193",
              holderName: "Sarah Nakato",
            },
          ],
          requests: [
            {
              id: "-NstubMessage0000031",
              body: "Hello, please can you withdraw my 182 day bill early? I need the money for school fees. Send it to my MTN number.",
              createdAt: ago(5),
            },
            {
              id: "-NstubMessage0000027",
              body: "Thank you for the help yesterday.",
              createdAt: ago(50),
            },
          ],
          open: true,
        }
      : { investments: [], accounts: [], requests: [], open: false };
  const later = <T>(value: () => T) =>
    new Promise<T>((resolve, reject) =>
      setTimeout(() => {
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new Error(String(error)));
        }
      }, delayMs),
    );
  const strip = (item: ClientAction & { clientId: string }): ClientAction => {
    const { clientId, ...action } = item;
    void clientId;
    return action;
  };

  return {
    actionsFor: (clientId) =>
      later(() => ({
        actions: actions.filter((item) => item.clientId === clientId).map(strip),
        allowed: [...allowed],
      })),
    limitsFor: (clientId) =>
      later(() => ({
        shared: SHARED,
        own: own.get(clientId) ?? null,
        applied: lower(SHARED, own.get(clientId) ?? null),
      })),
    withdrawalOptions: () => later(() => options),
    propose: (clientId, proposal) =>
      later(() => {
        if (!allowed.includes(proposal.kind)) throw new ActionError("forbidden");
        if (!proposal.reason.trim()) throw new ActionError("invalid");
        if (actions.some((item) => item.clientId === clientId && item.kind === proposal.kind && item.status === "proposed")) throw new ActionError("waiting");
        if (proposal.kind === "withdrawal_limits") {
          for (const key of LIMIT_KEYS) {
            const value = proposal.limits[key];
            const shared = SHARED[key];
            if (value !== null && shared !== null && value >= shared) throw new ActionError("not_below_shared");
          }
        }
        if (
          proposal.kind === "statement" &&
          (proposal.statement.from > proposal.statement.to || proposal.statement.to > new Date(now()).toISOString().slice(0, 10))
        ) {
          throw new ActionError("period");
        }
        actions.unshift({
          ...base,
          id: `-NstubAction${String(next++).padStart(8, "0")}`,
          clientId,
          kind: proposal.kind,
          reason: proposal.reason.trim(),
          proposedByName: me.name,
          proposedAt: new Date(now()).toISOString(),
          status: "proposed",
          limits: proposal.kind === "withdrawal_limits" ? proposal.limits : null,
          statement: proposal.kind === "statement" ? proposal.statement : null,
          withdrawal: proposal.kind === "withdrawal" ? proposal.withdrawal : null,
          yours: true,
        });
      }),
    decide: (actionId, decision, reason) =>
      later(() => {
        const action = actions.find((item) => item.id === actionId);
        if (!action || action.status !== "proposed") throw new ActionError("not_found");
        if (!allowed.includes(action.kind) || action.yours !== (decision === "withdrawn")) throw new ActionError("forbidden");
        if (!reason.trim()) throw new ActionError("invalid");
        action.status = decision;
        action.decidedByName = me.name;
        action.decidedAt = new Date(now()).toISOString();
        action.decisionReason = reason.trim();
        if (decision === "approved" && action.kind === "withdrawal_limits" && action.limits) {
          own.set(action.clientId, action.limits);
        }
      }),
  };
}
