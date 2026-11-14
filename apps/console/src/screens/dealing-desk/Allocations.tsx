import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import type { Currency } from "./api.ts";
import {
  allocationErrorMessage,
  type AllocationReport,
  type AllocationsApi,
  type InvestmentBills,
  type ProductAllocation,
} from "./allocations.ts";
import { formatDay, formatExact } from "./format.ts";
import { FIELD, Field } from "./Orders.tsx";
import { Row } from "./Position.tsx";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; report: AllocationReport };

const COUNT = new Intl.NumberFormat("en-GB");
const count = (n: number, one: string, many: string) => `${COUNT.format(n)} ${n === 1 ? one : many}`;

interface AllocationSectionProps {
  api: AllocationsApi;
  currency?: Currency;
}

/** Which bills stand behind client money, product by product. Read only. */
export function AllocationSection({ api, currency = "UGX" }: AllocationSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });

  const fetchReport = useCallback(
    (): Promise<Load> =>
      api.getReport(currency).then(
        (report) => ({ state: "ready", report }),
        (error: unknown) => ({ state: "failed", message: allocationErrorMessage(error) }),
      ),
    [api, currency],
  );

  useEffect(() => {
    let current = true;
    void fetchReport().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchReport]);

  const heading = (
    <h2 id="allocation-heading" className="m-0 text-xl font-extrabold">
      Bills behind client money
    </h2>
  );

  if (load.state === "loading") {
    return (
      <section aria-labelledby="allocation-heading" className="flex flex-col gap-3">
        {heading}
        <p role="status" className="m-0 text-md text-muted">
          Loading the allocation report
        </p>
      </section>
    );
  }
  if (load.state === "failed") {
    return (
      <section aria-labelledby="allocation-heading" className="flex flex-col gap-3">
        {heading}
        <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">The allocation report could not be loaded</h3>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchReport().then(setLoad)}>
            Try again
          </Button>
        </div>
      </section>
    );
  }

  const { report } = load;
  const nothing = report.products.every((p) => p.lots.length === 0 && p.investments === 0);
  return (
    <section aria-labelledby="allocation-heading" className="flex flex-col gap-4">
      <div>
        {heading}
        <p className="m-0 text-md leading-normal text-muted">
          Each settled purchase is shared out to the investments waiting for a bill, oldest payment first, every 15 minutes. Figures for{" "}
          {formatDay(report.on)}.
        </p>
      </div>
      {nothing ? (
        <p className="m-0 rounded-xl border border-border bg-surface p-5 text-md leading-normal">
          No bills are held and no investments are running in {report.currency} yet. Once a purchase settles, its bills are shared out here.
        </p>
      ) : (
        report.products.map((product) => <ProductCard key={product.productCode} product={product} />)
      )}
      <Lookup api={api} />
    </section>
  );
}

function ProductCard({ product }: { product: ProductAllocation }) {
  const id = `allocation-${product.productCode}`;
  const waiting = product.investmentsWaiting === 0 ? "None" : `${count(product.investmentsWaiting, "investment", "investments")}, ${formatExact(product.waiting)}`;
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <h3 id={id} className="m-0 text-[17px] font-extrabold">
        {product.productCode}
      </h3>
      <div className="flex flex-wrap gap-x-8 gap-y-2">
        <dl className="m-0 flex min-w-[260px] flex-1 flex-col gap-2">
          <Row name="Bills held, at cost" value={formatExact(product.costHeld)} />
          <Row name="Behind investments" value={formatExact(product.allocated)} />
          <Row name="Not shared out yet" value={formatExact(product.unallocated)} />
        </dl>
        <dl className="m-0 flex min-w-[260px] flex-1 flex-col gap-2">
          <Row name="Investments running" value={COUNT.format(product.investments)} />
          <Row name="Owed to them" value={formatExact(product.owed)} />
          <Row name="Waiting for a bill" value={waiting} />
        </dl>
      </div>
      {product.lots.length === 0 ? (
        <p className="m-0 text-sm text-muted">No bills of this product are held.</p>
      ) : (
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={`Bills held in ${product.productCode}`}>
          <table className="w-full min-w-[720px] border-collapse text-sm whitespace-nowrap">
            <caption className="sr-only">Bills held in {product.productCode}, oldest purchase first</caption>
            <thead>
              <tr className="text-left text-xs text-muted">
                {["Settled", "Matures", "Custodian reference", "Face value", "Cost", "Behind investments", "Not shared out", "Investments"].map((name) => (
                  <th key={name} scope="col" className="px-2 py-2 font-bold">
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {product.lots.map((lot) => (
                <tr key={lot.lotId} className="border-t border-divider tabular-nums">
                  <td className="px-2 py-2">{formatDay(lot.settledOn)}</td>
                  <td className="px-2 py-2">{formatDay(lot.maturesOn)}</td>
                  <th scope="row" className="px-2 py-2 text-left font-bold">
                    {lot.custodianReference}
                  </th>
                  <td className="px-2 py-2">{formatExact(lot.face)}</td>
                  <td className="px-2 py-2">{formatExact(lot.cost)}</td>
                  <td className="px-2 py-2">{formatExact(lot.allocated)}</td>
                  <td className="px-2 py-2">{formatExact(lot.unallocated)}</td>
                  <td className="px-2 py-2">{COUNT.format(lot.investments)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

type Found = { state: "idle" } | { state: "busy" } | { state: "failed"; message: string } | { state: "ready"; bills: InvestmentBills };

/** The bills behind one investment, for a question about it. */
function Lookup({ api }: { api: AllocationsApi }) {
  const [investmentId, setInvestmentId] = useState("");
  const [typed, setTyped] = useState<string | null>(null);
  const [found, setFound] = useState<Found>({ state: "idle" });

  async function submit(event: FormEvent) {
    event.preventDefault();
    const id = investmentId.trim();
    if (!id) {
      setTyped("Enter the investment's ID.");
      return;
    }
    setTyped(null);
    setFound({ state: "busy" });
    try {
      setFound({ state: "ready", bills: await api.billsBehind(id) });
    } catch (error) {
      setFound({ state: "failed", message: allocationErrorMessage(error) });
    }
  }

  return (
    <section aria-labelledby="bills-behind" className="flex max-w-[720px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <h3 id="bills-behind" className="m-0 text-[17px] font-extrabold">
        Bills behind one investment
      </h3>
      <form aria-labelledby="bills-behind" noValidate className="flex flex-wrap items-end gap-3" onSubmit={(event) => void submit(event)}>
        <div className="min-w-[240px] flex-1">
          <Field id="investment-id" label="Investment ID" error={typed ?? undefined}>
            {(control) => <Input {...control} className={FIELD} value={investmentId} onChange={(e) => setInvestmentId(e.target.value)} />}
          </Field>
        </div>
        <Button type="submit" disabled={found.state === "busy"}>
          Show bills
        </Button>
      </form>
      {found.state === "failed" ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {found.message}
        </p>
      ) : null}
      {found.state === "ready" ? <BillsList bills={found.bills} /> : null}
    </section>
  );
}

function BillsList({ bills }: { bills: InvestmentBills }) {
  if (bills.bills.length === 0 || bills.total === null) {
    return (
      <p className="m-0 text-md leading-normal text-muted">
        No bill stands behind {bills.investmentId} now. It is waiting for the next purchase, or it is not running.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <ul aria-label={`Bills behind ${bills.investmentId}`} className="m-0 flex list-none flex-col gap-2 p-0">
        {bills.bills.map((bill) => (
          <li key={bill.lotId} className="flex flex-wrap justify-between gap-2 border-t border-divider pt-2 text-md">
            <span>
              {bill.productCode}, {bill.custodianReference}, matures {formatDay(bill.maturesOn)}
            </span>
            <span className="font-bold tabular-nums">{formatExact(bill.cost)}</span>
          </li>
        ))}
      </ul>
      <p className="m-0 text-md font-bold">In all {formatExact(bills.total)}, at cost</p>
    </div>
  );
}
