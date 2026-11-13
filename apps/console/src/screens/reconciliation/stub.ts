import {
  BankTransferError,
  type Amount,
  type BankTransfersApi,
  type QueueItem,
  type RefuseReason,
  type Review,
  type StatementLine,
  type TransferAction,
  type TransferDetail,
} from "./api.ts";
import { formatExact } from "./format.ts";

/**
 * Sample bank transfers for `pnpm dev` and previews. Not real people or money,
 * and not connected to Finvest. Decisions change only this browser tab's copy.
 * The rules here stand in for the backend's: the screen itself decides nothing.
 */

export type StubCase = "sample" | "empty" | "error";

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });

interface Stored {
  id: string;
  investmentId: string;
  amount: Amount;
  sentOn: string;
  bankName: string;
  transactionNumber: string;
  submittedAt: string;
  payer: string;
  status: "in_review" | "confirmed" | "failed";
  review: (Omit<Review, "reviewedByYou" | "approvedByYou"> & { by: string; approvedBy: string | null; lineId: string | null }) | null;
  lines: StatementLine[];
}

function ago(minutes: number, now: Date): string {
  return new Date(now.getTime() - minutes * 60_000).toISOString();
}

function day(offset: number, now: Date): string {
  return new Date(now.getTime() + offset * 86_400_000).toISOString().slice(0, 10);
}

const line = (id: string, valueDate: string, minor: number, reference: string, narrative: string): StatementLine => ({
  id,
  accountReference: "Stanbic trust 9030017",
  valueDate,
  amount: ugx(minor),
  reference,
  narrative,
});

const OTHER = "sample-colleague";

function sample(now: Date, me: string): Stored[] {
  return [
    {
      id: "-OdBank000000000001A",
      investmentId: "-OdInv0000000000001A",
      amount: ugx(2_500_000),
      sentOn: day(-1, now),
      bankName: "Stanbic Bank",
      transactionNumber: "FT2631700482",
      submittedAt: ago(95, now),
      payer: "DAVID MUGISHA",
      status: "in_review",
      review: null,
      lines: [
        line("-OdLine000000000001A", day(-1, now), 2_500_000, "FT2631700482", "RTGS IN DAVID MUGISHA FINVEST"),
        line("-OdLine000000000002A", day(-3, now), 2_500_000, "EFT 88123", "EFT IN NAKATO SARAH"),
      ],
    },
    {
      id: "-OdBank000000000002A",
      investmentId: "-OdInv0000000000002A",
      amount: ugx(10_000_000),
      sentOn: day(0, now),
      bankName: "Centenary Bank",
      transactionNumber: "CB/11/4471",
      submittedAt: ago(22, now),
      payer: "RUTH AKELLO",
      status: "in_review",
      review: null,
      lines: [],
    },
    {
      id: "-OdBank000000000003A",
      investmentId: "-OdInv0000000000003A",
      amount: ugx(5_000_000),
      sentOn: day(-2, now),
      bankName: "dfcu Bank",
      transactionNumber: "DF77310925",
      submittedAt: ago(300, now),
      payer: "JOHN OKELLO",
      status: "in_review",
      review: { outcome: "matched", reason: null, reviewedAt: ago(40, now), approvedAt: null, by: OTHER, approvedBy: null, lineId: "-OdLine000000000003A" },
      lines: [line("-OdLine000000000003A", day(-2, now), 5_000_000, "DF77310925", "TRANSFER FROM JOHN OKELLO DFCU")],
    },
    {
      id: "-OdBank000000000004A",
      investmentId: "-OdInv0000000000004A",
      amount: ugx(1_200_000),
      sentOn: day(-1, now),
      bankName: "Absa Bank",
      transactionNumber: "AB0099871",
      submittedAt: ago(180, now),
      payer: "GRACE NAMUBIRU",
      status: "in_review",
      review: { outcome: "matched", reason: null, reviewedAt: ago(12, now), approvedAt: null, by: me, approvedBy: null, lineId: "-OdLine000000000004A" },
      lines: [line("-OdLine000000000004A", day(-1, now), 1_200_000, "AB0099871", "IBT GRACE NAMUBIRU")],
    },
  ];
}

/** A drawing of a bank receipt, so the screen has something to show beside the line. */
function receiptImage(stored: Stored): string {
  const rows = [
    ["Transaction", stored.transactionNumber],
    ["Date", stored.sentOn],
    ["From", stored.payer],
    ["To", "FINVEST CLIENT TRUST"],
    ["Amount", formatExact(stored.amount)],
  ];
  const text = rows
    .map(
      ([label, value], index) =>
        `<text x="32" y="${150 + index * 46}" font-size="15" fill="#6b6b6b">${label}</text>` +
        `<text x="388" y="${150 + index * 46}" font-size="17" font-weight="700" text-anchor="end">${value}</text>`,
    )
    .join("");
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="420" height="440" font-family="Helvetica, Arial, sans-serif">` +
    `<rect width="420" height="440" fill="#ffffff"/><rect width="420" height="70" fill="#14325c"/>` +
    `<text x="32" y="44" font-size="20" font-weight="700" fill="#ffffff">${stored.bankName}</text>` +
    `<text x="32" y="106" font-size="16" font-weight="700">Funds transfer receipt</text>${text}` +
    `<text x="32" y="410" font-size="12" fill="#6b6b6b">Sample receipt. Not a real transaction.</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export function createStubBankTransfersApi(me: string, delayMs = 150, which: StubCase = "sample", now = () => new Date()): BankTransfersApi {
  const transfers = which === "sample" ? sample(now(), me) : [];
  const used = new Set<string>();
  const replies = new Map<string, TransferDetail>();

  const settle = <T>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        try {
          if (which === "error") throw new BankTransferError("unavailable");
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new Error(String(error)));
        }
      }, delayMs),
    );

  function find(id: string): Stored {
    const found = transfers.find((transfer) => transfer.id === id);
    if (!found) throw new BankTransferError("not_found");
    return found;
  }

  function stage(stored: Stored): QueueItem["stage"] {
    if (stored.status === "confirmed") return "confirmed";
    if (stored.status === "failed") return "refused";
    if (!stored.review) return "to_match";
    return stored.review.approvedBy ? "confirming" : "to_approve";
  }

  function allowed(stored: Stored): TransferAction[] {
    if (stored.status !== "in_review") return [];
    if (!stored.review) return ["match", "refuse"];
    return stored.review.approvedBy === null && stored.review.by !== me ? ["approve"] : [];
  }

  function detail(stored: Stored): TransferDetail {
    const { review } = stored;
    const reviewed = review !== null;
    return {
      id: stored.id,
      investmentId: stored.investmentId,
      amount: stored.amount,
      sentOn: stored.sentOn,
      bankName: stored.bankName,
      transactionNumber: stored.transactionNumber,
      submittedAt: stored.submittedAt,
      stage: stage(stored),
      receipt: { contentType: "image/png", byteSize: 184_320, url: receiptImage(stored) },
      review: review
        ? {
            outcome: review.outcome,
            reason: review.reason,
            reviewedAt: review.reviewedAt,
            reviewedByYou: review.by === me,
            approvedAt: review.approvedAt,
            approvedByYou: review.approvedBy === null ? null : review.approvedBy === me,
          }
        : null,
      matchedLine: review?.lineId ? (stored.lines.find((found) => found.id === review.lineId) ?? null) : null,
      candidates: reviewed ? [] : stored.lines.filter((found) => !used.has(found.id)),
      allowed: allowed(stored),
    };
  }

  /** Replays the answer to a key already used, as the backend's idempotency does. */
  function once(key: string, command: () => TransferDetail): TransferDetail {
    const replay = replies.get(key);
    if (replay) return replay;
    const reply = command();
    replies.set(key, reply);
    return reply;
  }

  return {
    listQueue: () =>
      settle(() =>
        transfers
          .filter((stored) => stored.status === "in_review")
          .map((stored) => {
            const { id, amount, sentOn, bankName, submittedAt } = detail(stored);
            return { id, amount, sentOn, bankName, submittedAt, stage: stage(stored) };
          })
          .sort((a, b) => b.submittedAt.localeCompare(a.submittedAt)),
      ),
    getTransfer: (id) => settle(() => detail(find(id))),
    match: (key, id, statementLineId) =>
      settle(() =>
        once(key, () => {
          const stored = find(id);
          if (!allowed(stored).includes("match")) throw new BankTransferError("conflict");
          if (used.has(statementLineId)) throw new BankTransferError("line_already_used");
          if (!stored.lines.some((found) => found.id === statementLineId)) throw new BankTransferError("line_not_found");
          used.add(statementLineId);
          stored.review = { outcome: "matched", reason: null, reviewedAt: now().toISOString(), approvedAt: null, by: me, approvedBy: null, lineId: statementLineId };
          return detail(stored);
        }),
      ),
    refuse: (key, id, reason: RefuseReason) =>
      settle(() =>
        once(key, () => {
          const stored = find(id);
          if (!allowed(stored).includes("refuse")) throw new BankTransferError("conflict");
          stored.review = { outcome: "not_matched", reason, reviewedAt: now().toISOString(), approvedAt: null, by: me, approvedBy: null, lineId: null };
          stored.status = "failed";
          return detail(stored);
        }),
      ),
    approve: (key, id) =>
      settle(() =>
        once(key, () => {
          const stored = find(id);
          if (stored.review?.by === me) throw new BankTransferError("same_person");
          if (!allowed(stored).includes("approve") || !stored.review) throw new BankTransferError("conflict");
          stored.review = { ...stored.review, approvedAt: now().toISOString(), approvedBy: me };
          // The service confirms it moments later. The sample does it at once.
          stored.status = "confirmed";
          return detail(stored);
        }),
      ),
  };
}
