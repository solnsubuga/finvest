import { kampalaToday } from "@/autoinvest/schedule";

import { monthLabel, periodProblem, taxYearLabel } from "./periods";
import { DocumentItem, DocumentsClient, DocumentsError } from "./types";

/**
 * An in-memory stand-in for the documents routes. Nothing here is real, and
 * `DocumentsProvider` keeps it out of production builds.
 *
 * The two demo accounts have three monthly statements, one statement they
 * asked for and two receipts. An account made by signing up has none, which
 * shows the empty state. A number ending in 99 cannot reach the service. A
 * statement asked for from before 2020 fails to build, to show that state.
 */

const SEEDED = new Set(["+256772123456", "+256701000200"]);
const KAMPALA_OFFSET_MS = 3 * 60 * 60 * 1000;
/** How many times a new statement is read as pending before it is ready. */
const PENDING_READS = 2;

type Options = {
  delayMs?: number;
  now?: () => Date;
};

function shiftMonth(day: string, months: number, date: number): string {
  const [y, m] = day.split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1 + months, date)).toISOString().slice(0, 10);
}

/** 06:10 Kampala on a day, as an ISO time. */
function morning(day: string): string {
  return new Date(Date.parse(`${day}T06:10:00Z`) - KAMPALA_OFFSET_MS).toISOString();
}

function seed(today: string, now: Date): DocumentItem[] {
  const monthly = [1, 2, 3].map((back): DocumentItem => {
    const from = shiftMonth(today, -back, 1);
    const to = shiftMonth(today, -back + 1, 0);
    const sent = shiftMonth(today, -back + 1, 1);
    const label = monthLabel(from);
    return {
      id: `demo-monthly-${back}`,
      kind: "monthly_statement",
      status: "ready",
      dateFrom: from,
      dateTo: to,
      requestedAt: morning(sent),
      finishedAt: morning(sent),
      fileName: `Finvest statement ${label}.pdf`,
      byteSize: 48_000,
      notFinalFrom: null,
      reference: `ST-demo-monthly-${back}`,
      note: null,
    };
  });
  const earlier = new Date(now.getTime() - 25 * 60 * 1000).toISOString();
  const requested: DocumentItem = {
    id: "demo-requested-1",
    kind: "requested_statement",
    status: "ready",
    dateFrom: shiftMonth(today, -3, 1),
    dateTo: today,
    requestedAt: earlier,
    finishedAt: earlier,
    fileName: "Finvest statement requested.pdf",
    byteSize: 61_000,
    notFinalFrom: today,
    reference: "ST-demo-requested-1",
    note: null,
  };
  const receipts = [
    { id: "demo-receipt-1", reference: "RC-Pq30vNa2", day: shiftMonth(today, -1, 10) },
    { id: "demo-receipt-2", reference: "RC-Pq7Tz2Lm", day: shiftMonth(today, -4, 18) },
  ].map(
    ({ id, reference, day }): DocumentItem => ({
      id,
      kind: "receipt",
      status: "ready",
      dateFrom: null,
      dateTo: null,
      requestedAt: morning(day),
      finishedAt: morning(day),
      fileName: `Finvest receipt ${reference}.pdf`,
      byteSize: 22_000,
      notFinalFrom: null,
      reference,
      note: null,
    }),
  );
  // The last tax year that has ended, 1 July to 30 June, issued on 2 July.
  const [year, month] = today.split("-").map(Number);
  const starts = (year ?? 1970) - ((month ?? 1) >= 7 ? 1 : 2);
  const certificate: DocumentItem = {
    id: "demo-tax-certificate-1",
    kind: "tax_certificate",
    status: "ready",
    dateFrom: `${starts}-07-01`,
    dateTo: `${starts + 1}-06-30`,
    requestedAt: morning(`${starts + 1}-07-02`),
    finishedAt: morning(`${starts + 1}-07-02`),
    fileName: `Finvest tax certificate ${taxYearLabel(`${starts}-07-01`)}.pdf`,
    byteSize: 41_000,
    notFinalFrom: null,
    reference: "TC-demo-tax-certificate-1",
    note: null,
  };
  const note: DocumentItem = {
    id: "demo-investment-note-1",
    kind: "investment_note",
    status: "ready",
    dateFrom: null,
    dateTo: null,
    requestedAt: morning(shiftMonth(today, -1, 11)),
    finishedAt: morning(shiftMonth(today, -1, 11)),
    fileName: "Finvest investment note IN-Pq31wXb7.pdf",
    byteSize: 24_000,
    notFinalFrom: null,
    reference: "IN-Pq31wXb7",
    note: "started",
  };
  const endNote: DocumentItem = {
    ...note,
    id: "demo-investment-note-2",
    requestedAt: morning(shiftMonth(today, -2, 3)),
    finishedAt: morning(shiftMonth(today, -2, 3)),
    fileName: "Finvest investment end note IN-Pq2mKc84.pdf",
    reference: "IN-Pq2mKc84",
    note: "ended",
  };
  return [requested, ...monthly, certificate, note, endNote, ...receipts];
}

export function createStubDocumentsClient(phone: string | null, options: Options = {}) {
  const { delayMs = 150, now = () => new Date() } = options;
  const wait = () => new Promise((resolve) => setTimeout(resolve, delayMs));
  const today = () => kampalaToday(now());
  let documents: DocumentItem[] | null = null;
  const reads = new Map<string, number>();
  const keys = new Map<string, string>();
  const asked: string[] = [];
  const downloaded: string[] = [];

  function all(): DocumentItem[] {
    if (!phone || phone.endsWith("99")) {
      throw new DocumentsError("unavailable");
    }
    documents ??= SEEDED.has(phone) ? seed(today(), now()) : [];
    return documents;
  }

  /** A new statement is pending for a couple of reads, then ready or failed. */
  function advance(document: DocumentItem): DocumentItem {
    if (document.status !== "pending") {
      return document;
    }
    const count = (reads.get(document.id) ?? 0) + 1;
    reads.set(document.id, count);
    if (count < PENDING_READS) {
      return document;
    }
    const failed = (document.dateFrom ?? "") < "2020-01-01";
    const finished: DocumentItem = failed
      ? { ...document, status: "failed", finishedAt: now().toISOString() }
      : {
          ...document,
          status: "ready",
          finishedAt: now().toISOString(),
          fileName: "Finvest statement requested.pdf",
          byteSize: 58_000,
          notFinalFrom: (document.dateTo ?? "") >= today() ? today() : null,
        };
    documents = all().map((item) => (item.id === document.id ? finished : item));
    return finished;
  }

  const client: DocumentsClient & { asked: string[]; downloaded: string[] } = {
    asked,
    downloaded,
    async listDocuments() {
      await wait();
      return all()
        .map(advance)
        .sort((a, b) => b.requestedAt.localeCompare(a.requestedAt));
    },
    async getDocument(id) {
      await wait();
      const found = all().find((item) => item.id === id);
      if (!found) {
        throw new DocumentsError("not_found");
      }
      return advance(found);
    },
    async requestStatement(dateFrom, dateTo, idempotencyKey) {
      await wait();
      const existing = keys.get(idempotencyKey);
      const items = all();
      if (existing) {
        const first = items.find((item) => item.id === existing);
        if (first) {
          return first;
        }
      }
      const problem = periodProblem(dateFrom, dateTo, today());
      if (problem) {
        throw new DocumentsError("validation_failed", problem);
      }
      if (asked.length >= 10) {
        throw new DocumentsError("rate_limited");
      }
      const document: DocumentItem = {
        id: `demo-asked-${asked.length + 1}`,
        kind: "requested_statement",
        status: "pending",
        dateFrom,
        dateTo,
        requestedAt: now().toISOString(),
        finishedAt: null,
        fileName: null,
        byteSize: null,
        notFinalFrom: null,
        reference: `ST-demo-asked-${asked.length + 1}`,
        note: null,
      };
      asked.push(document.id);
      keys.set(idempotencyKey, document.id);
      documents = [document, ...items];
      return document;
    },
    async download(document) {
      await wait();
      if (document.status !== "ready") {
        throw new DocumentsError("not_found");
      }
      downloaded.push(document.id);
    },
  };
  return client;
}
