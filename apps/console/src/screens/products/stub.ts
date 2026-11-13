import { CatalogueError, type CatalogueApi, type NewProposal, type Product, type Proposal, type Rate } from "./api.ts";
import { previewClientRate } from "./format.ts";

/**
 * A sample catalogue for `pnpm dev` and previews, with the products and rates
 * the backend seeds. Not connected to Finvest. Commands change only this
 * browser tab's copy, and the checks here stand in for the backend's.
 */

export interface StubOptions {
  /** The signed-in person's staff ID. */
  me: string;
  /** The Kampala business day. */
  today: string;
  /** "empty" starts with no rate changes waiting. */
  sample?: "default" | "empty";
}

const COLLEAGUE = "-StaffSampleFinance01";

function rate(effectiveFrom: string, annualRateBps: number, taxBps: number): Rate {
  return { effectiveFrom, annualRateBps, taxBps, clientRatePercent: previewClientRate(annualRateBps, taxBps).replace("%", "") };
}

function sampleProducts(): Product[] {
  return [
    {
      id: "FvProduct1TREA91_001",
      code: "TREA-91",
      name: "91 Day Treasury Bill",
      kind: "treasury_bill",
      currency: "UGX",
      termDays: 91,
      minimum: { minor: 100_000, currency: "UGX" },
      interestPaid: "at_maturity",
      status: "live",
      rate: null,
      nextRate: null,
    },
    {
      id: "FvProduct2TREA182_01",
      code: "TREA-182",
      name: "182 Day Treasury Bill",
      kind: "treasury_bill",
      currency: "UGX",
      termDays: 182,
      minimum: { minor: 200_000, currency: "UGX" },
      interestPaid: "at_maturity",
      status: "live",
      rate: null,
      nextRate: null,
    },
    {
      id: "FvProduct3USDBOND_01",
      code: "USD-BOND",
      name: "USD Fixed Income",
      kind: "fixed_income",
      currency: "USD",
      termDays: null,
      minimum: { minor: 500_00, currency: "USD" },
      interestPaid: "monthly",
      status: "paused",
      rate: null,
      nextRate: null,
    },
  ];
}

function sampleRates(): Map<string, Rate[]> {
  return new Map([
    ["TREA-91", [rate("2026-01-01", 1250, 1500)]],
    ["TREA-182", [rate("2026-07-01", 1375, 1500), rate("2026-01-01", 1400, 1500)]],
    ["USD-BOND", [rate("2026-01-01", 725, 500)]],
  ]);
}

export function createStubCatalogueApi({ me, today, sample = "default" }: StubOptions): CatalogueApi {
  const products = sampleProducts();
  const rates = sampleRates();
  const proposals: Proposal[] = [];
  let counter = 0;

  function add(proposedBy: string, draft: NewProposal, at: string): Proposal {
    counter += 1;
    const proposal: Proposal = {
      id: `-RateProposalSample${counter}`,
      productCode: draft.productCode,
      rate: rate(draft.effectiveFrom, draft.annualRateBps, draft.taxBps),
      reason: draft.reason,
      status: "proposed",
      proposedBy,
      proposedAt: at,
      decidedBy: null,
      decidedAt: null,
      decisionNote: null,
    };
    proposals.unshift(proposal);
    return proposal;
  }

  if (sample === "default") {
    add(
      COLLEAGUE,
      {
        productCode: "TREA-91",
        annualRateBps: 1300,
        taxBps: 1500,
        effectiveFrom: "2026-12-01",
        reason: "The 91 day yield rose to 13.4% at the 11 November auction.",
      },
      "2026-11-13T07:40:00Z",
    );
  }

  const clone = <T>(value: T): T => structuredClone(value);

  function view(product: Product): Product {
    const history = rates.get(product.code) ?? [];
    const inForce = history.find((r) => r.effectiveFrom <= today) ?? null;
    const later = history.filter((r) => r.effectiveFrom > today);
    return { ...product, rate: inForce, nextRate: later.at(-1) ?? null };
  }

  function waiting(proposalId: string): Proposal {
    const proposal = proposals.find((p) => p.id === proposalId);
    if (!proposal) throw new CatalogueError("not_found");
    if (proposal.status !== "proposed") throw new CatalogueError("conflict");
    return proposal;
  }

  function decide(proposal: Proposal, status: Proposal["status"], note: string | null): Proposal {
    proposal.status = status;
    proposal.decidedBy = me;
    proposal.decidedAt = new Date().toISOString();
    proposal.decisionNote = note;
    return clone(proposal);
  }

  return {
    listProducts: () => Promise.resolve(products.map(view)),
    rateHistory: (code) => Promise.resolve(clone(rates.get(code) ?? [])),
    listProposals: (status) => Promise.resolve(clone(proposals.filter((p) => !status || p.status === status))),
    propose(draft) {
      if (!draft.reason.trim()) return Promise.reject(new CatalogueError("reason_required"));
      if (draft.effectiveFrom <= today) return Promise.reject(new CatalogueError("effective_date_not_in_future"));
      if (draft.annualRateBps < 1 || draft.annualRateBps > 5000 || draft.taxBps > 9999) {
        return Promise.reject(new CatalogueError("out_of_range"));
      }
      const history = rates.get(draft.productCode) ?? [];
      if (history.some((r) => r.effectiveFrom === draft.effectiveFrom)) {
        return Promise.reject(new CatalogueError("date_already_taken"));
      }
      return Promise.resolve(clone(add(me, draft, new Date().toISOString())));
    },
    approve(proposalId) {
      try {
        const proposal = waiting(proposalId);
        if (proposal.proposedBy === me) throw new CatalogueError("forbidden");
        const history = rates.get(proposal.productCode) ?? [];
        history.push(proposal.rate);
        history.sort((a, b) => b.effectiveFrom.localeCompare(a.effectiveFrom));
        rates.set(proposal.productCode, history);
        return Promise.resolve(decide(proposal, "approved", null));
      } catch (error) {
        return Promise.reject(error instanceof Error ? error : new CatalogueError("unavailable"));
      }
    },
    sendBack(proposalId, note) {
      try {
        const proposal = waiting(proposalId);
        if (proposal.proposedBy === me) throw new CatalogueError("forbidden");
        if (!note.trim()) throw new CatalogueError("reason_required");
        return Promise.resolve(decide(proposal, "sent_back", note.trim()));
      } catch (error) {
        return Promise.reject(error instanceof Error ? error : new CatalogueError("unavailable"));
      }
    },
    withdraw(proposalId) {
      try {
        const proposal = waiting(proposalId);
        if (proposal.proposedBy !== me) throw new CatalogueError("forbidden");
        return Promise.resolve(decide(proposal, "withdrawn", null));
      } catch (error) {
        return Promise.reject(error instanceof Error ? error : new CatalogueError("unavailable"));
      }
    },
  };
}
