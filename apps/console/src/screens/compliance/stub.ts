import {
  ComplianceError,
  type Case,
  type CaseDetail,
  type CaseOutcome,
  type CaseSource,
  type ComplianceApi,
  type Match,
  type Proposal,
  type Run,
  type Summary,
  type WaitingPayout,
} from "./api.ts";

/**
 * Sample compliance cases for `pnpm dev` and previews. Not real clients,
 * listed people or money. It keeps the backend's rules that a screen can show:
 * one waiting proposal per case, an outcome that fits the case, the proposer
 * never decides, and only the proposer withdraws.
 *
 * `empty` is a new system with nothing waiting, `off` is the same with no
 * screening provider set up, and `error` cannot reach Finvest.
 */
export type StubCase = "default" | "empty" | "off" | "error";

const OTHER = { id: "sample-staff-grace", name: "Grace Auma" };

export const OUTCOMES: Record<CaseSource, readonly CaseOutcome[]> = {
  screening: ["not_a_match", "pep_accepted", "sanctioned"],
  monitoring: ["no_concern", "report_to_fia"],
};

interface Sample {
  case: Case;
  run: Run | null;
  matches: Match[];
  detail: Record<string, unknown>;
  proposals: Proposal[];
}

function proposal(id: string, by: { id: string; name: string }, outcome: CaseOutcome, reason: string, at: string): Proposal {
  return {
    id,
    outcome,
    reason,
    proposedBy: by.id,
    proposedByName: by.name,
    proposedAt: at,
    status: "proposed",
    decidedBy: null,
    decidedByName: null,
    decidedAt: null,
    decisionNote: null,
  };
}

function samples(me: { id: string; name: string }): Sample[] {
  const run = (id: string, at: string, reason: Run["reason"]): Run => ({
    id,
    reason,
    requestedAt: at,
    status: "possible_match",
    finishedAt: at,
    provider: "development-stub",
    failureCode: null,
  });
  const base = { status: "open" as const, outcome: null, closedAt: null, waiting: null };
  const records: Sample[] = [
    {
      case: {
        ...base,
        id: "-CaseSample000000001",
        client: { id: "-ClientSample0000011", name: "Sarah Nakato", accountNumber: "FV 2034 5521" },
        source: "screening",
        code: "possible_match",
        openedAt: "2026-11-12T07:40:00Z",
      },
      run: run("-RunSample0000000001", "2026-11-12T07:40:00Z", "onboarding"),
      matches: [
        { entityId: "S360-88213", name: "Sara Nakato", category: "sanctions", strength: 71, sources: ["UN Security Council"], known: false },
      ],
      detail: {},
      proposals: [
        proposal(
          "-PropSample000000001",
          OTHER,
          "not_a_match",
          "The listed Sara Nakato was born in 1961 and lives in Kenya. Our client was born in 1994, on her national ID.",
          "2026-11-13T08:15:00Z",
        ),
      ],
    },
    {
      case: {
        ...base,
        id: "-CaseSample000000002",
        client: { id: "-ClientSample0000012", name: "Joseph Okello", accountNumber: "FV 2034 7702" },
        source: "screening",
        code: "possible_match",
        openedAt: "2026-11-13T02:16:00Z",
      },
      run: run("-RunSample0000000002", "2026-11-13T02:16:00Z", "rescreen"),
      matches: [
        { entityId: "S360-10442", name: "Joseph Okello", category: "pep", strength: 94, sources: ["Uganda Parliament members"], known: false },
        { entityId: "S360-55190", name: "Joseph O. Okello", category: "adverse_media", strength: 62, sources: ["Adverse media"], known: true },
      ],
      detail: {},
      proposals: [],
    },
    {
      case: {
        ...base,
        id: "-CaseSample000000003",
        client: { id: "-ClientSample0000013", name: "Ruth Achieng", accountNumber: "FV 2035 0118" },
        source: "monitoring",
        code: "split_deposits",
        openedAt: "2026-11-13T11:02:00Z",
      },
      run: null,
      matches: [],
      detail: {
        deposit_ids: ["-DepSample0000000031", "-DepSample0000000032", "-DepSample0000000033"],
        total_minor: 21_500_000,
        threshold: 20_000_000,
        days: 7,
      },
      proposals: [
        proposal(
          "-PropSample000000003",
          me,
          "report_to_fia",
          "Three mobile money deposits from different numbers in four days, just under the threshold. Filed on goAML, reference STR-2026-0141.",
          "2026-11-13T14:30:00Z",
        ),
      ],
    },
    {
      case: {
        ...base,
        id: "-CaseSample000000004",
        client: { id: "-ClientSample0000014", name: "Peter Mugisha", accountNumber: "FV 2034 9930" },
        source: "monitoring",
        code: "large_deposit",
        openedAt: "2026-11-14T06:20:00Z",
      },
      run: null,
      matches: [],
      detail: { deposit_id: "-DepSample0000000041", amount_minor: 45_000_000, threshold: 20_000_000 },
      proposals: [],
    },
    {
      case: {
        ...base,
        id: "-CaseSample000000005",
        client: { id: "-ClientSample0000015", name: "Esther Namubiru", accountNumber: "FV 2033 4410" },
        source: "monitoring",
        code: "quick_withdrawal",
        openedAt: "2026-11-09T09:00:00Z",
        status: "closed",
        outcome: "no_concern",
        closedAt: "2026-11-10T10:12:00Z",
      },
      run: null,
      matches: [],
      detail: {
        payout_id: "-PaySample0000000051",
        amount_minor: 8_000_000,
        deposits_minor: 10_000_000,
        deposit_ids: ["-DepSample0000000051"],
        days: 7,
      },
      proposals: [
        {
          ...proposal(
            "-PropSample000000005",
            OTHER,
            "no_concern",
            "School fees paid out, as the client told support when she paid in. Invoice seen.",
            "2026-11-09T15:00:00Z",
          ),
          status: "approved",
          decidedBy: me.id,
          decidedByName: me.name,
          decidedAt: "2026-11-10T10:12:00Z",
        },
      ],
    },
  ];
  return records;
}

const PAYOUTS: WaitingPayout[] = [
  {
    id: "-PaySample0000000011",
    client: { id: "-ClientSample0000011", name: "Sarah Nakato", accountNumber: "FV 2034 5521" },
    kind: "mobile_money",
    amount: { minor: 1_200_000, currency: "UGX" },
    createdAt: "2026-11-13T09:05:00Z",
    standing: { outcome: "possible_match", screenedAt: "2026-11-12T07:40:00Z", caseId: "-CaseSample000000001", pep: false },
  },
  {
    id: "-PaySample0000000016",
    client: { id: "-ClientSample0000016", name: "Moses Kato", accountNumber: "FV 2035 2207" },
    kind: "bank",
    amount: { minor: 15_000_000, currency: "UGX" },
    createdAt: "2026-11-14T07:58:00Z",
    standing: { outcome: "pending", screenedAt: null, caseId: null, pep: false },
  },
];

export function createStubComplianceApi(me: { id: string; name: string }, sample: StubCase = "default", now: () => string = () => new Date().toISOString()): ComplianceApi {
  const records = sample === "default" ? samples(me) : [];
  const payouts = sample === "default" ? PAYOUTS : [];
  let n = 0;
  const fail = () => Promise.reject(new ComplianceError("unavailable"));
  const find = (caseId: string) => records.find((record) => record.case.id === caseId);
  const byProposal = (proposalId: string) => records.find((record) => record.proposals.some((p) => p.id === proposalId));

  function detail(record: Sample): CaseDetail {
    const waiting = record.proposals.find((p) => p.status === "proposed") ?? null;
    record.case = { ...record.case, waiting };
    const open = record.case.status === "open";
    return structuredClone({
      case: record.case,
      run: record.run,
      matches: record.matches,
      detail: record.detail,
      proposals: [...record.proposals].reverse(),
      standing:
        record.case.source === "screening"
          ? { outcome: open ? "possible_match" : record.case.outcome === "sanctioned" ? "confirmed_match" : "clear", screenedAt: record.run?.finishedAt ?? null, caseId: open ? record.case.id : null, pep: record.case.outcome === "pep_accepted" }
          : null,
      outcomes: OUTCOMES[record.case.source],
      canPropose: open && waiting === null,
      canDecide: open && waiting !== null && waiting.proposedBy !== me.id,
      yours: waiting?.proposedBy === me.id,
    } satisfies CaseDetail);
  }

  function decide(proposalId: string, status: Proposal["status"], note: string | null): CaseDetail {
    const record = byProposal(proposalId);
    const found = record?.proposals.find((p) => p.id === proposalId);
    if (!record || !found) throw new ComplianceError("not_found");
    if (found.status !== "proposed") throw new ComplianceError("case_closed");
    const mine = found.proposedBy === me.id;
    if (status === "withdrawn" ? !mine : mine) throw new ComplianceError(status === "withdrawn" ? "forbidden" : "same_person");
    Object.assign(found, { status, decidedBy: me.id, decidedByName: me.name, decidedAt: now(), decisionNote: note });
    if (status === "approved") {
      record.case = { ...record.case, status: "closed", outcome: found.outcome, closedAt: now() };
    }
    return detail(record);
  }

  const counted = (): Summary => {
    const open = records.filter((record) => record.case.status === "open");
    return {
      screeningOn: sample !== "off",
      provider: sample === "off" ? "none" : "development-stub",
      openCases: open.length,
      openScreeningCases: open.filter((record) => record.case.source === "screening").length,
      openMonitoringCases: open.filter((record) => record.case.source === "monitoring").length,
      waitingForSecondPerson: open.filter((record) => record.proposals.some((p) => p.status === "proposed")).length,
      screenedLast30Days: sample === "default" ? 1_284 : 0,
      unavailableLast30Days: sample === "default" ? 3 : 0,
      notRunLast30Days: sample === "off" ? 12 : 0,
      pending: sample === "default" ? 1 : 0,
      lastScreenedAt: sample === "default" ? "2026-11-14T02:15:00Z" : null,
      payoutsWaiting: payouts.length,
    };
  };

  if (sample === "error") {
    return { summary: fail, listCases: fail, getCase: fail, waitingPayouts: fail, propose: fail, approve: fail, sendBack: fail, withdraw: fail };
  }
  return {
    summary: () => Promise.resolve(counted()),
    listCases: (filter) => {
      const shown = records
        .filter((record) => record.case.status === filter.status && (filter.source === null || record.case.source === filter.source))
        .map((record) => detail(record).case);
      shown.sort((a, b) => (filter.status === "open" ? a.openedAt.localeCompare(b.openedAt) : b.openedAt.localeCompare(a.openedAt)));
      return Promise.resolve(shown);
    },
    getCase: (caseId) => {
      const record = find(caseId);
      return record ? Promise.resolve(detail(record)) : Promise.reject(new ComplianceError("not_found"));
    },
    waitingPayouts: () => Promise.resolve(structuredClone(payouts)),
    propose: (caseId, outcome, reason) => {
      const record = find(caseId);
      if (!record) return Promise.reject(new ComplianceError("not_found"));
      if (record.case.status !== "open") return Promise.reject(new ComplianceError("case_closed"));
      if (record.proposals.some((p) => p.status === "proposed")) return Promise.reject(new ComplianceError("proposal_waiting"));
      if (!OUTCOMES[record.case.source].includes(outcome)) return Promise.reject(new ComplianceError("outcome_does_not_fit_case"));
      record.proposals.push(proposal(`-PropSampleNew${String(++n).padStart(6, "0")}`, me, outcome, reason, now()));
      return Promise.resolve(detail(record));
    },
    approve: (id) => new Promise((resolve) => resolve(decide(id, "approved", null))),
    sendBack: (id, note) => new Promise((resolve) => resolve(decide(id, "sent_back", note))),
    withdraw: (id) => new Promise((resolve) => resolve(decide(id, "withdrawn", null))),
  };
}
