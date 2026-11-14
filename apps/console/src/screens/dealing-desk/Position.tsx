import { cn } from "@/lib.ts";
import type { Cover, Desk } from "./api.ts";
import { bufferNote, COVER_LABELS, formatAuctionDay, formatShort } from "./format.ts";

const COVER_STYLES: Record<Cover, string> = {
  covered: "bg-success-surface text-success",
  bid_needed: "bg-warning-surface text-warning-text",
  on_order: "bg-surface-sunken text-black",
  surplus: "bg-surface-sunken text-black",
};

/** The four figures at the top of the desk. */
export function Figures({ desk }: { desk: Desk }) {
  const { buffer, earlyWithdrawals } = desk;
  const custodian =
    desk.matchesCustodian === null
      ? "Not compared with the custodian yet"
      : desk.matchesCustodian
        ? "Matches the custodian"
        : "Does not match the custodian";
  return (
    <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
      <Figure name="Client money to place" value={formatShort(desk.moneyToPlace)} note="Paid in and not yet in bills" />
      <Figure name="Bills held for clients" value={formatShort(desk.billsHeld)} note={custodian} warn={desk.matchesCustodian === false} />
      <Figure
        name="Liquidity buffer"
        value={formatShort(buffer.balance)}
        note={bufferNote(buffer.level, buffer.target, buffer.floor)}
        warn={buffer.level !== "on_target"}
      />
      <Figure
        name="Early withdrawals asked"
        value={formatShort(earlyWithdrawals.asked)}
        note={earlyWithdrawals.clients === 1 ? "1 client this week" : `${earlyWithdrawals.clients} clients this week`}
      />
    </dl>
  );
}

function Figure({ name, value, note, warn = false }: { name: string; value: string; note: string; warn?: boolean }) {
  return (
    <div className={cn("rounded-[18px] bg-surface p-4", warn ? "border-2 border-warning p-[15px]" : "border border-border")}>
      <dt className="text-sm text-muted">{name}</dt>
      <dd className="m-0 text-[22px] font-extrabold tracking-[-0.4px] tabular-nums">{value}</dd>
      <dd className={cn("m-0 text-xs font-bold", warn ? "text-warning-text" : "text-muted")}>{note}</dd>
    </div>
  );
}

/** What clients are owed against the bills held, product by product. */
export function PositionTable({ desk }: { desk: Desk }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="Position by product">
      <table className="w-full min-w-[600px] border-collapse text-md">
        <caption className="sr-only">Position by product</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {["Product", "Clients owed", "Bills held", "To buy", "Next auction", "Status"].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {desk.positions.map((position) => (
            <tr key={position.productCode} className="border-t border-divider tabular-nums">
              <th scope="row" className="px-3 py-3 text-left font-bold">
                {position.productName}
              </th>
              <td className="px-3 py-3">{formatShort(position.clientsOwed, false)}</td>
              <td className="px-3 py-3">{formatShort(position.billsHeld, false)}</td>
              <td className="px-3 py-3">{formatShort(position.toBuy, false)}</td>
              <td className="px-3 py-3">{position.nextAuction ? formatAuctionDay(position.nextAuction) : "On demand"}</td>
              <td className="px-3 py-3">
                <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold whitespace-nowrap", COVER_STYLES[position.cover])}>
                  {COVER_LABELS[position.cover]}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** How early withdrawals are paid, as the rules in force say. */
export function BufferRules({ desk }: { desk: Desk }) {
  const { buffer } = desk;
  const days = buffer.payWithinWorkingDays === 1 ? "1 working day" : `${buffer.payWithinWorkingDays} working days`;
  return (
    <section aria-labelledby="buffer-rules" className="flex flex-col gap-2.5 rounded-xl border border-border bg-surface p-5">
      <h2 id="buffer-rules" className="m-0 text-[17px] font-extrabold">
        Paying early withdrawals
      </h2>
      <dl className="m-0 flex flex-col gap-2.5">
        <Row name="From the liquidity buffer" value={`Up to ${formatShort(buffer.fromBufferUpTo)} each`} />
        <Row name="Above that" value="Sell on the secondary market" />
        <Row name="Client is paid within" value={days} />
        <Row name="Buffer after this week" value={formatShort(buffer.afterPromised)} />
      </dl>
      <p className="m-0 text-sm leading-[1.45] text-muted">
        If the buffer falls below {formatShort(buffer.floor)}, new early withdrawals queue and the desk is alerted.
      </p>
    </section>
  );
}

export function Row({ name, value }: { name: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
      <dt className="text-muted">{name}</dt>
      <dd className="m-0 text-right font-bold tabular-nums">{value}</dd>
    </div>
  );
}
