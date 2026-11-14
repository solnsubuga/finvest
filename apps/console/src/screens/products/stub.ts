import {
  CatalogueError,
  type CatalogueApi,
  type NewProposal,
  type Product,
  type ProductChange,
  type ProductReach,
  type Proposal,
  type Rate,
} from "./api.ts";
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
  /** "empty" starts with no rate or product changes waiting. "offline" acts as if Finvest could not be reached. */
  sample?: "default" | "empty" | "offline";
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

/** Sample counts for who a change reaches, by product. A product not here reaches nobody. */
const SAMPLE_REACH: Record<string, ProductReach> = {
  "TREA-91": { runningInvestments: 1_284, awaitingPayment: 37, clients: 912, openGoals: 146, activePlans: 88, pausedPlans: 9 },
  "TREA-182": { runningInvestments: 655, awaitingPayment: 12, clients: 498, openGoals: 61, activePlans: 23, pausedPlans: 2 },
};
const NOBODY: ProductReach = { runningInvestments: 0, awaitingPayment: 0, clients: 0, openGoals: 0, activePlans: 0, pausedPlans: 0 };

export function createStubCatalogueApi(options: StubOptions): CatalogueApi {
  if (options.sample !== "offline") return createSample(options);
  const offline = () => Promise.reject(new CatalogueError("unavailable"));
  return Object.fromEntries(Object.keys(createSample({ ...options, sample: "empty" })).map((name) => [name, offline])) as unknown as CatalogueApi;
}

function createSample({ me, today, sample = "default" }: StubOptions): CatalogueApi {
  const products = sampleProducts();
  const rates = sampleRates();
  const proposals: Proposal[] = [];
  const changes: ProductChange[] = [];
  let counter = 0;

  function addChange(proposedBy: string, change: Omit<ProductChange, "id" | "status" | "proposedBy" | "proposedAt" | "decidedBy" | "decidedAt" | "decisionNote">, at: string): ProductChange {
    counter += 1;
    const made: ProductChange = {
      ...change,
      id: `-ProductChangeSample${counter}`,
      status: "proposed",
      proposedBy,
      proposedAt: at,
      decidedBy: null,
      decidedAt: null,
      decisionNote: null,
    };
    changes.unshift(made);
    return made;
  }

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

  if (sample === "default") {
    addChange(
      COLLEAGUE,
      {
        action: "create",
        productCode: "TREA-364",
        name: "364 Day Treasury Bill",
        kind: "treasury_bill",
        currency: "UGX",
        termDays: 364,
        minimum: { minor: 500_000, currency: "UGX" },
        interestPaid: "at_maturity",
        productStatus: "draft",
        firstRate: rate(today, 1500, 1500),
        reason: "Bank of Uganda auctions the 364 day bill every fortnight and clients ask for a longer term.",
      },
      "2026-11-13T09:05:00Z",
    );
  }

  const clone = <T>(value: T): T => structuredClone(value);

  function waitingChange(changeId: string): ProductChange {
    const change = changes.find((c) => c.id === changeId);
    if (!change) throw new CatalogueError("not_found");
    if (change.status !== "proposed") throw new CatalogueError("conflict");
    return change;
  }

  function oneWaitingFor(code: string): void {
    if (changes.some((c) => c.status === "proposed" && c.productCode === code)) throw new CatalogueError("conflict");
  }

  function productFor(code: string): Product {
    const product = products.find((p) => p.code === code);
    if (!product) throw new CatalogueError("not_found");
    return product;
  }

  function attempt<T>(work: () => T): Promise<T> {
    try {
      return Promise.resolve(work());
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new CatalogueError("unavailable"));
    }
  }

  function decideChange(change: ProductChange, status: ProductChange["status"], note: string | null): ProductChange {
    change.status = status;
    change.decidedBy = me;
    change.decidedAt = new Date().toISOString();
    change.decisionNote = note;
    return clone(change);
  }

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
      // Like the backend: a change to the rate and tax already in force on that day is refused.
      const before = history
        .filter((r) => r.effectiveFrom < draft.effectiveFrom)
        .sort((a, b) => b.effectiveFrom.localeCompare(a.effectiveFrom))[0];
      if (before && before.annualRateBps === draft.annualRateBps && before.taxBps === draft.taxBps) {
        return Promise.reject(new CatalogueError("nothing_changes"));
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
    listProductChanges: (status) => Promise.resolve(clone(changes.filter((c) => !status || c.status === status))),
    proposeNewProduct: (draft) =>
      attempt(() => {
        if (!draft.reason.trim()) throw new CatalogueError("reason_required");
        if (products.some((p) => p.code === draft.code) || changes.some((c) => c.status === "proposed" && c.action === "create" && c.productCode === draft.code)) {
          throw new CatalogueError("code_taken");
        }
        if (draft.annualRateBps < 1 || draft.annualRateBps > 5000 || draft.taxBps > 9999) throw new CatalogueError("out_of_range");
        return clone(
          addChange(
            me,
            {
              action: "create",
              productCode: draft.code,
              name: draft.name,
              kind: draft.kind,
              currency: draft.currency,
              termDays: draft.termDays,
              minimum: { minor: draft.minimumMinor, currency: draft.currency },
              interestPaid: draft.interestPaid,
              productStatus: "draft",
              firstRate: rate(today, draft.annualRateBps, draft.taxBps),
              reason: draft.reason,
            },
            new Date().toISOString(),
          ),
        );
      }),
    proposeNewTerms: (draft) =>
      attempt(() => {
        const product = productFor(draft.productCode);
        if (!draft.reason.trim()) throw new CatalogueError("reason_required");
        if (product.status !== "draft" && draft.kind !== product.kind) throw new CatalogueError("kind_fixed");
        if (product.status !== "draft" && draft.interestPaid !== product.interestPaid) throw new CatalogueError("interest_paid_fixed");
        if (
          draft.name === product.name &&
          draft.kind === product.kind &&
          draft.termDays === product.termDays &&
          draft.minimumMinor === product.minimum.minor &&
          draft.interestPaid === product.interestPaid
        ) {
          throw new CatalogueError("terms_unchanged");
        }
        oneWaitingFor(product.code);
        return clone(
          addChange(
            me,
            {
              action: "edit",
              productCode: product.code,
              name: draft.name,
              kind: draft.kind,
              currency: product.currency,
              termDays: draft.termDays,
              minimum: { minor: draft.minimumMinor, currency: product.currency },
              interestPaid: draft.interestPaid,
              productStatus: product.status,
              firstRate: null,
              reason: draft.reason,
            },
            new Date().toISOString(),
          ),
        );
      }),
    proposeStatus: (code, to, reason) =>
      attempt(() => {
        const product = productFor(code);
        if (!reason.trim()) throw new CatalogueError("reason_required");
        if (to === "paused" && product.status !== "live") throw new CatalogueError("not_live");
        if (to === "live") {
          if (product.status === "live") throw new CatalogueError("already_live");
          if (product.currency !== "UGX") throw new CatalogueError("currency_not_open");
          if (!view(product).rate) throw new CatalogueError("no_rate");
        }
        oneWaitingFor(code);
        return clone(
          addChange(
            me,
            {
              action: to === "live" ? "open" : "pause",
              productCode: code,
              name: product.name,
              kind: product.kind,
              currency: product.currency,
              termDays: product.termDays,
              minimum: product.minimum,
              interestPaid: product.interestPaid,
              productStatus: to,
              firstRate: null,
              reason,
            },
            new Date().toISOString(),
          ),
        );
      }),
    approveChange: (changeId) =>
      attempt(() => {
        const change = waitingChange(changeId);
        if (change.proposedBy === me) throw new CatalogueError("forbidden");
        const terms = {
          name: change.name,
          kind: change.kind,
          termDays: change.termDays,
          minimum: change.minimum,
          interestPaid: change.interestPaid,
          status: change.productStatus,
        };
        if (change.action === "create") {
          products.push({ id: `-ProductSample${changeId}`, code: change.productCode, currency: change.currency, rate: null, nextRate: null, ...terms });
          rates.set(change.productCode, change.firstRate ? [change.firstRate] : []);
        } else {
          Object.assign(productFor(change.productCode), terms);
        }
        return decideChange(change, "approved", null);
      }),
    sendBackChange: (changeId, note) =>
      attempt(() => {
        const change = waitingChange(changeId);
        if (change.proposedBy === me) throw new CatalogueError("forbidden");
        if (!note.trim()) throw new CatalogueError("reason_required");
        return decideChange(change, "sent_back", note.trim());
      }),
    withdrawChange: (changeId) =>
      attempt(() => {
        const change = waitingChange(changeId);
        if (change.proposedBy !== me) throw new CatalogueError("forbidden");
        return decideChange(change, "withdrawn", null);
      }),
    reach: (code) => attempt(() => clone(SAMPLE_REACH[productFor(code).code] ?? NOBODY)),
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

/** The sample a page's `?sample=` asks for: `empty`, `offline`, or the default. */
export function sampleFrom(search: string): "default" | "empty" | "offline" {
  const asked = new URLSearchParams(search).get("sample");
  return asked === "empty" || asked === "offline" ? asked : "default";
}
