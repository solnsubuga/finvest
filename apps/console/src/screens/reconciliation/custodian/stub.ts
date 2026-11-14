import {
  CustodianError,
  type CheckDetail,
  type CheckItem,
  type CheckSummary,
  type CustodianChecksApi,
  type StatementRow,
} from "./api.ts";

/**
 * Sample custodian checks for `pnpm dev` and previews. Not real holdings, and not connected to
 * Finvest. The comparison here stands in for the backend's: the screen itself decides nothing.
 */

export type CustodianStubCase = "sample" | "empty" | "error" | "off";

const OTHER = "sample-colleague";

/** What the dealing desk holds in the samples, by maturity date. */
const HELD: Record<string, { face: number; products: string[] }> = {
  "2026-12-03": { face: 1_250_000_000, products: ["TREA-91"] },
  "2027-02-04": { face: 800_000_000, products: ["TREA-91", "TREA-182"] },
  "2027-05-13": { face: 2_000_000_000, products: ["TREA-182"] },
  "2027-11-11": { face: 450_000_000, products: ["TREA-364"] },
};

interface Held {
  detail: CheckDetail;
  heldFor: typeof HELD;
}

function row(lineNo: number, instrument: string, code: string | null, face: string, matures: string | null, location: string, source: string): StatementRow {
  const digits = face.replaceAll(",", "");
  const parsed = /^\d+$/.test(digits) ? Number(digits) : null;
  const day = matures && /^\d{4}-\d{2}-\d{2}$/.test(matures) ? matures : null;
  return {
    id: `-OdCustodianRow00${String(lineNo).padStart(3, "0")}`,
    lineNo,
    instrument,
    code,
    faceValueText: face,
    currencyText: "UGX",
    maturesOnText: matures,
    location,
    sourceText: source,
    currency: parsed !== null && day ? "UGX" : null,
    faceMinor: parsed !== null && day ? parsed : null,
    maturesOn: parsed !== null && day ? day : null,
    problem: parsed === null ? "row_amount_unreadable" : day ? null : "row_maturity_unreadable",
    correctedFaceMinor: null,
    correctedMaturesOn: null,
    leftOut: false,
    correctedBy: null,
    correctionReason: null,
  };
}

function sampleRows(): StatementRow[] {
  return [
    row(1, "GOU T-Bill 91D", "UG0000001234", "1,250,000,000", "2026-12-03", "page 1", "UG0000001234  GOU T-BILL 91D  03/12/2026  1,250,000,000  1,231,450,000"),
    row(2, "GOU T-Bill 182D", "UG0000001288", "80,000,000", "2027-02-04", "page 1", "UG0000001288  GOU T-BILL 182D  04/02/2027  800,000,000  771,200,000"),
    row(3, "GOU T-Bill 182D", "UG0000001301", "2,000,000,000", "2027-05-13", "page 1", "UG0000001301  GOU T-BILL 182D  13/05/2027  2,000,000,000  1,904,000,000"),
    row(4, "GOU T-Bond 2Y", "UG0000000977", "300,000,000", "2028-08-24", "page 2", "UG0000000977  GOU T-BOND 2Y 14.25%  24/08/2028  300,000,000"),
    row(5, "Cash at custodian", null, "see note", null, "page 2", "CASH BALANCE  UGX  see note 4"),
  ];
}

function counted(r: StatementRow): { face: number; day: string } | null {
  if (r.leftOut) return null;
  const face = r.correctedFaceMinor ?? r.faceMinor;
  const day = r.correctedMaturesOn ?? r.maturesOn;
  return face !== null && day ? { face, day } : null;
}

/** The stand-in comparison, as the backend's: by maturity date, then rows not counted. */
export function compareSample(rows: StatementRow[], held: typeof HELD, notes: Record<string, string>): CheckItem[] {
  const theirs = new Map<string, { face: number; rows: string[] }>();
  const unreadable: CheckItem[] = [];
  for (const r of rows) {
    if (r.leftOut) continue;
    const c = counted(r);
    if (!c) {
      const key = `row:${r.id}`;
      unreadable.push(item(key, "row_unreadable", null, 0, 0, [], [r.id], r.problem, notes));
      continue;
    }
    const at = theirs.get(c.day) ?? { face: 0, rows: [] };
    at.face += c.face;
    at.rows.push(r.id);
    theirs.set(c.day, at);
  }
  const days = [...new Set([...Object.keys(held), ...theirs.keys()])].sort();
  const items = days.map((day) => {
    const ours = held[day];
    const at = theirs.get(day);
    const finvest = ours?.face ?? 0;
    const custodian = at?.face ?? 0;
    const kind = !at ? "missing_at_custodian" : !ours ? "not_in_finvest" : finvest === custodian ? "match" : "amount_differs";
    return item(`maturity:UGX:${day}`, kind, day, finvest, custodian, ours?.products ?? [], at?.rows ?? [], null, notes);
  });
  return [...items, ...unreadable];
}

function item(
  key: string,
  kind: CheckItem["kind"],
  maturesOn: string | null,
  finvest: number,
  custodian: number,
  productCodes: string[],
  rowIds: string[],
  problem: string | null,
  notes: Record<string, string>,
): CheckItem {
  return {
    key,
    kind,
    currency: maturesOn ? "UGX" : null,
    maturesOn,
    finvestFaceMinor: finvest,
    custodianFaceMinor: custodian,
    differenceMinor: custodian - finvest,
    productCodes,
    rowIds,
    problem,
    needsNote: kind !== "match",
    note: notes[key] ?? null,
  };
}

function summary(id: string, statementDate: string, now: Date, overrides: Partial<CheckSummary>): CheckSummary {
  return {
    id,
    statementDate,
    status: "to_review",
    fileContentType: "application/pdf",
    fileBytes: 182_000,
    uploadedBy: OTHER,
    uploadedAt: new Date(now.getTime() - 3 * 3_600_000).toISOString(),
    reader: "claude:sample",
    readStatementDate: statementDate,
    custodianName: "Sample Custodian Bank Uganda",
    accountReference: "FIN-CUST-001",
    amountScale: "one",
    failureCode: null,
    submittedBy: null,
    submitNote: null,
    signedOffBy: null,
    signedOffAt: null,
    signOffNote: null,
    sentBackBy: null,
    sentBackNote: null,
    allowed: [],
    ...overrides,
  };
}

function day(offset: number, now: Date): string {
  return new Date(now.getTime() + offset * 86_400_000).toISOString().slice(0, 10);
}

function allowedFor(check: CheckSummary, me: string): CheckSummary["allowed"] {
  if (check.status === "to_review") return ["correct", "note", "submit"];
  if (check.status === "waiting_sign_off" && check.submittedBy !== me) return ["sign_off", "send_back"];
  return [];
}

export function createStubCustodianChecksApi(me: string, delay = 150, which: CustodianStubCase = "sample", now = new Date()): CustodianChecksApi {
  const checks = new Map<string, Held>();
  const notes = new Map<string, Record<string, string>>();
  let made = 0;

  function add(check: CheckSummary, rows: StatementRow[], itemNotes: Record<string, string> = {}) {
    notes.set(check.id, itemNotes);
    checks.set(check.id, { detail: { check, rows, items: [], unexplained: 0 }, heldFor: HELD });
    recompute(check.id);
  }

  function recompute(id: string) {
    const held = checks.get(id);
    if (!held) return;
    const { detail } = held;
    const items = detail.check.status === "unreadable" || detail.check.status === "reading" ? [] : compareSample(detail.rows, held.heldFor, notes.get(id) ?? {});
    const check = { ...detail.check, allowed: allowedFor(detail.check, me) };
    held.detail = { check, rows: detail.rows, items, unexplained: items.filter((i) => i.needsNote && !i.note?.trim()).length };
  }

  if (which === "sample") {
    add(summary("-OdCustodianCheck0003", day(-1, now), now, { uploadedBy: me }), sampleRows());
    const waiting = sampleRows().map((r) =>
      r.lineNo === 2
        ? { ...r, correctedFaceMinor: 800_000_000, correctedBy: OTHER, correctionReason: "The statement shows 800,000,000; the reader dropped a zero" }
        : r.lineNo === 5
          ? { ...r, leftOut: true, correctedBy: OTHER, correctionReason: "Cash line, not a bill" }
          : r,
    );
    add(
      summary("-OdCustodianCheck0002", day(-8, now), now, {
        status: "waiting_sign_off",
        submittedBy: OTHER,
        submitNote: "Every line checked against the PDF.",
        uploadedAt: new Date(now.getTime() - 8 * 86_400_000).toISOString(),
      }),
      waiting,
      {
        "maturity:UGX:2027-11-11": "Bought on the 6th; the custodian books it a day after settlement.",
        "maturity:UGX:2028-08-24": "Bond held for Finvest's own account, not a client product.",
      },
    );
    add(
      summary("-OdCustodianCheck0001", day(-15, now), now, {
        status: "unreadable",
        failureCode: "statement_not_a_spreadsheet",
        fileContentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        reader: "claude:sample",
        readStatementDate: null,
        custodianName: null,
        accountReference: null,
        amountScale: null,
        uploadedAt: new Date(now.getTime() - 15 * 86_400_000).toISOString(),
      }),
      [],
    );
  }

  const wait = <T>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        if (which === "error") reject(new CustodianError("unavailable"));
        else {
          try {
            resolve(value());
          } catch (error) {
            reject(error instanceof Error ? error : new CustodianError("unavailable"));
          }
        }
      }, delay),
    );

  function found(id: string): Held {
    const held = checks.get(id);
    if (!held) throw new CustodianError("not_found");
    return held;
  }

  function set(id: string, change: Partial<CheckSummary>) {
    const held = found(id);
    held.detail = { ...held.detail, check: { ...held.detail.check, ...change } };
    recompute(id);
    return found(id).detail;
  }

  return {
    list: () =>
      wait(() => ({
        readingOn: which !== "off",
        checks: [...checks.values()].map((held) => held.detail.check).sort((a, b) => b.uploadedAt.localeCompare(a.uploadedAt)),
      })),
    get: (id) => wait(() => found(id).detail),
    upload: (_key, statementDate, file) =>
      wait(() => {
        if (which === "off") throw new CustodianError("custodian_reading_off");
        if (statementDate > day(0, now)) throw new CustodianError("statement_date_in_the_future");
        made += 1;
        const id = `-OdCustodianCheckN${String(made).padStart(3, "0")}`;
        add(summary(id, statementDate, now, { uploadedBy: me, uploadedAt: new Date().toISOString(), fileBytes: file.size }), sampleRows());
        return found(id).detail.check;
      }),
    fileLink: () => wait(() => "about:blank"),
    correctRow: (id, rowId, correction) =>
      wait(() => {
        const held = found(id);
        if (held.detail.check.status !== "to_review") throw new CustodianError("check_not_in_review");
        if (!correction.leftOut && correction.faceMinor === null && correction.maturesOn === null) throw new CustodianError("correction_empty");
        held.detail = {
          ...held.detail,
          rows: held.detail.rows.map((r) =>
            r.id === rowId
              ? {
                  ...r,
                  leftOut: correction.leftOut,
                  correctedFaceMinor: correction.leftOut ? null : correction.faceMinor,
                  correctedMaturesOn: correction.leftOut ? null : correction.maturesOn,
                  correctedBy: me,
                  correctionReason: correction.reason,
                }
              : r,
          ),
        };
        recompute(id);
        return found(id).detail;
      }),
    note: (id, itemKey, note) =>
      wait(() => {
        if (found(id).detail.check.status !== "to_review") throw new CustodianError("check_not_in_review");
        notes.set(id, { ...(notes.get(id) ?? {}), [itemKey]: note });
        recompute(id);
        return found(id).detail;
      }),
    submit: (_key, id, note) =>
      wait(() => {
        const { detail } = found(id);
        if (detail.check.status !== "to_review") throw new CustodianError("check_not_in_review");
        if (detail.unexplained > 0) throw new CustodianError("differences_unexplained");
        return set(id, { status: "waiting_sign_off", submittedBy: me, submitNote: note });
      }),
    signOff: (_key, id, note) =>
      wait(() => {
        const { detail } = found(id);
        if (detail.check.status !== "waiting_sign_off") throw new CustodianError("check_not_waiting");
        if (detail.check.submittedBy === me) throw new CustodianError("check_needs_second_person");
        return set(id, { status: "signed_off", signedOffBy: me, signedOffAt: new Date().toISOString(), signOffNote: note });
      }),
    sendBack: (_key, id, reason) =>
      wait(() => {
        const { detail } = found(id);
        if (detail.check.status !== "waiting_sign_off") throw new CustodianError("check_not_waiting");
        if (detail.check.submittedBy === me) throw new CustodianError("check_needs_second_person");
        return set(id, { status: "to_review", submittedBy: null, submitNote: null, sentBackBy: me, sentBackNote: reason });
      }),
  };
}
