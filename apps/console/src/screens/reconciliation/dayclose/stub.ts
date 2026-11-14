import type { Amount } from "../api.ts";
import {
  DayCloseError,
  type Blocker,
  type Day,
  type DayCloseApi,
  type Movement,
  type PotException,
  type PotLine,
  type Position,
  type Source,
  addDays,
  kampalaToday,
} from "./api.ts";

/**
 * A sample day close for `pnpm dev` and previews. Not real money, and not connected to Finvest.
 * What staff do changes only this browser tab's copy. The rules here stand in for the backend's:
 * the screen itself decides nothing.
 *
 * `sample` is yesterday with work left: the MTN statement is missing and the trust account has a
 * deposit and a fee nobody has paired. `prepared` is yesterday with every statement in, prepared
 * by a colleague and waiting for a second person. `empty` is a day with nothing left open.
 */

export type DayCloseStubCase = "sample" | "prepared" | "empty" | "error";

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });
const COLLEAGUE = "sample-colleague";

interface Pot {
  source: Source;
  accountReference: string;
  name: string;
  opening: number;
  ledgerClosing: number;
  /** The closing balance the day's real statement prints. The stub refuses any other. */
  printedClosing: number;
  statementClosing: number | null;
  lines: PotLine[];
  movements: Movement[];
  /** Who imported the day's statement. They cannot approve the close. */
  importedBy: string | null;
}

interface State {
  day: string;
  pots: Pot[];
  close: { id: string; status: "prepared" | "closed"; preparedBy: string; decidedBy: string | null } | null;
}

function line(id: string, valueDate: string, direction: "in" | "out", minor: number, reference: string, hint: string): PotLine {
  return { id, valueDate, direction, amount: ugx(minor), reference, counterpartyHint: hint, suggestedMovements: [] };
}

function movement(entryId: string, effectiveDate: string, direction: "in" | "out", minor: number, reference: string, entryType: string): Movement {
  return { id: `${entryId}:1`, entryId, lineNo: 1, effectiveDate, direction, amount: ugx(minor), reference, entryType };
}

function samplePots(day: string, stubCase: DayCloseStubCase): Pot[] {
  const allIn = stubCase !== "sample";
  const clean = stubCase === "empty";
  return [
    {
      source: "iotec",
      accountReference: "mtn",
      name: "ioTec wallet",
      opening: 12_400_000,
      ledgerClosing: 15_150_000,
      printedClosing: 15_150_000,
      statementClosing: allIn ? 15_150_000 : null,
      lines: [],
      movements: [],
      importedBy: allIn ? COLLEAGUE : null,
    },
    {
      source: "iotec",
      accountReference: "airtel",
      name: "ioTec wallet",
      opening: 3_200_000,
      ledgerClosing: 4_000_000,
      printedClosing: 4_000_000,
      statementClosing: 4_000_000,
      lines: [],
      movements: [],
      importedBy: COLLEAGUE,
    },
    {
      source: "bank",
      accountReference: "trust",
      name: "Trust bank account",
      opening: 482_500_000,
          // A client's deposit the ledger has under another reference, and the bank's monthly fee,
      // which finance has not posted yet. The open differences explain the gap exactly.
      ledgerClosing: 507_500_000,
      printedClosing: clean ? 507_500_000 : 507_485_000,
      statementClosing: clean ? 507_500_000 : 507_485_000,
      lines: clean
        ? []
        : [
            line("sample-line-deposit", day, "in", 2_500_000, "FT2631600123", "TRANSFER FROM S NAMUKASA"),
            line("sample-line-fee", day, "out", 15_000, "CHG/NOV/9030017", "MONTHLY SERVICE CHARGE"),
          ],
      movements: clean ? [] : [movement("-OdEntry0000000000031", day, "in", 2_500_000, "-OdBank000000000021A", "payment.deposit_confirmed")],
      importedBy: COLLEAGUE,
    },
  ];
}

function signed(direction: "in" | "out", minor: number): number {
  return direction === "in" ? minor : -minor;
}

function exceptionsOf(pot: Pot): PotException[] {
  if (pot.statementClosing === null) return [];
  const lines: PotException[] = pot.lines.map((each) => ({
    kind: "missing_in_ledger",
    difference: ugx(signed(each.direction, each.amount.minor)),
    lines: [
      {
        ...each,
        suggestedMovements: pot.movements
          .filter((found) => found.direction === each.direction && found.amount.minor === each.amount.minor)
          .map((found) => found.id),
      },
    ],
    movements: [],
  }));
  const movements: PotException[] = pot.movements.map((each) => ({
    kind: "missing_in_statement",
    difference: ugx(-signed(each.direction, each.amount.minor)),
    lines: [],
    movements: [each],
  }));
  return [...lines, ...movements];
}

function positionOf(pot: Pot): Position {
  const exceptions = exceptionsOf(pot);
  const open = exceptions.reduce((total, each) => total + each.difference.minor, 0);
  return {
    source: pot.source,
    accountReference: pot.accountReference,
    name: pot.name,
    statementClosing: pot.statementClosing === null ? null : ugx(pot.statementClosing),
    ledgerClosing: ugx(pot.ledgerClosing),
    openTotal: ugx(open),
    openCount: exceptions.length,
    unexplained: pot.statementClosing === null ? null : ugx(pot.statementClosing - pot.ledgerClosing - open),
    exceptions,
  };
}

function blockersOf(pots: Pot[]): Blocker[] {
  const found: Blocker[] = [];
  if (pots.some((pot) => pot.statementClosing === null)) found.push("statement_missing");
  if (pots.some((pot) => { const unexplained = positionOf(pot).unexplained; return unexplained !== null && unexplained.minor !== 0; }))
    found.push("difference_unexplained");
  return found;
}

/** Reads a balance as the statement prints it: "1,500,000". */
function parseBalance(text: string): number | null {
  const cleaned = text.trim().replaceAll(",", "");
  return /^\d{1,15}$/.test(cleaned) ? Number(cleaned) : null;
}

export function createStubDayCloseApi(me: string, delayMs = 150, stubCase: DayCloseStubCase = "sample", now: () => Date = () => new Date()): DayCloseApi {
  const yesterday = addDays(kampalaToday(now()), -1);
  const state: State = {
    day: yesterday,
    pots: samplePots(yesterday, stubCase),
    close: stubCase === "prepared" ? { id: "sample-close", status: "prepared", preparedBy: COLLEAGUE, decidedBy: null } : null,
  };
  const wait = <T,>(work: () => T): Promise<T> =>
    new Promise((resolve, reject) => {
      setTimeout(() => {
        try {
          resolve(work());
        } catch (error) {
          reject(error instanceof Error ? error : new DayCloseError("unavailable"));
        }
      }, delayMs);
    });
  const fail = <T,>(): Promise<T> => wait(() => {
    throw new DayCloseError("unavailable");
  });

  function sampleDay(date: string): State {
    if (date === state.day) return state;
    // Days before the sample are closed and square. Days after wait for it.
    const pots = samplePots(date, "empty").map((pot) => (date > state.day ? { ...pot, statementClosing: null } : pot));
    return { day: date, pots, close: date < state.day ? { id: `sample-close-${date}`, status: "closed", preparedBy: COLLEAGUE, decidedBy: COLLEAGUE } : null };
  }

  function view(date: string): Day {
    const found = sampleDay(date);
    const blockers: Blocker[] = found.close ? [] : [...(date > state.day ? (["previous_day_not_closed"] as Blocker[]) : []), ...blockersOf(found.pots)];
    return {
      date,
      lastClosed: state.close?.status === "closed" ? state.day : addDays(state.day, -1),
      close: found.close && {
        id: found.close.id,
        status: found.close.status,
        preparedByYou: found.close.preparedBy === me,
        decidedByYou: found.close.decidedBy === null ? null : found.close.decidedBy === me,
      },
      blockers,
      positions: found.pots.map(positionOf),
    };
  }

  function ended(date: string) {
    if (date >= kampalaToday(now())) throw new DayCloseError("day_not_ended");
  }

  function open(date: string): State {
    if (date !== state.day) throw new DayCloseError(date < state.day ? "close_not_open" : "previous_day_not_closed");
    return state;
  }

  if (stubCase === "error") {
    return { getDay: fail, refresh: fail, importStatement: fail, match: fail, prepare: fail, approve: fail, sendBack: fail };
  }

  return {
    getDay: (date) => wait(() => view(date)),
    refresh: (date) => wait(() => ended(date)),
    importStatement: (upload) =>
      wait(() => {
        if (upload.statementDate >= kampalaToday(now())) throw new DayCloseError("statement_day_not_over");
        if (upload.file.size === 0 || upload.file.size > 2 * 1024 * 1024) throw new DayCloseError("statement_file_size", "file");
        const opening = parseBalance(upload.openingBalance);
        if (opening === null) throw new DayCloseError("statement_amount_invalid", "openingBalance");
        const closing = parseBalance(upload.closingBalance);
        if (closing === null) throw new DayCloseError("statement_amount_invalid", "closingBalance");
        const found = open(upload.statementDate);
        const pot = found.pots.find((each) => each.source === upload.source && each.accountReference === upload.accountReference);
        if (!pot) throw new DayCloseError("invalid");
        if (pot.statementClosing !== null) throw new DayCloseError("statement_already_imported");
        if (opening !== pot.opening) throw new DayCloseError("statement_opening_balance_differs", "openingBalance");
        if (closing !== pot.printedClosing) throw new DayCloseError("statement_does_not_add_up");
        pot.statementClosing = closing;
        pot.importedBy = me;
        return { id: `sample-statement-${pot.accountReference}`, businessDate: upload.statementDate, lineCount: 14 };
      }),
    match: ({ statementLineId, movement: chosen, reason }) =>
      wait(() => {
        if (!reason.trim()) throw new DayCloseError("invalid");
        const pot = state.pots.find((each) => each.lines.some((found) => found.id === statementLineId));
        if (!pot || state.close) throw new DayCloseError("match_not_found");
        const paired = pot.lines.find((found) => found.id === statementLineId);
        const other = pot.movements.find((found) => found.id === chosen.id);
        if (!paired || !other) throw new DayCloseError("movement_already_matched");
        if (paired.amount.minor !== other.amount.minor || paired.direction !== other.direction) throw new DayCloseError("match_not_same_money");
        pot.lines = pot.lines.filter((found) => found !== paired);
        pot.movements = pot.movements.filter((found) => found !== other);
      }),
    prepare: (date) =>
      wait(() => {
        ended(date);
        const found = open(date);
        if (found.close) throw new DayCloseError("close_not_open");
        const blocker = blockersOf(found.pots)[0];
        if (blocker) throw new DayCloseError(blocker);
        found.close = { id: "sample-close", status: "prepared", preparedBy: me, decidedBy: null };
      }),
    approve: (date) =>
      wait(() => {
        const found = open(date);
        if (found.close?.status !== "prepared") throw new DayCloseError("close_not_prepared");
        if (found.close.preparedBy === me) throw new DayCloseError("close_needs_second_person");
        if (found.pots.some((pot) => pot.importedBy === me)) throw new DayCloseError("close_needs_someone_who_did_not_import");
        found.close = { ...found.close, status: "closed", decidedBy: me };
      }),
    sendBack: (date, reason) =>
      wait(() => {
        if (!reason.trim()) throw new DayCloseError("invalid");
        const found = open(date);
        if (found.close?.status !== "prepared") throw new DayCloseError("close_not_prepared");
        found.close = null;
      }),
  };
}
