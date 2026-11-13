import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { catalogueErrorMessage, notConnectedCatalogueApi, type CatalogueApi, type Product, type ProductStatus } from "./api.ts";
import {
  formatBps,
  formatDay,
  formatExact,
  formatTerm,
  INTEREST_PAID_LABELS,
  KIND_LABELS,
  PRODUCT_STATUS_LABELS,
} from "./format.ts";
import { createStubCatalogueApi } from "./stub.ts";

const SUMMARY = "What clients can invest in, at what rate, and what they see after tax.";

const STATUS_STYLES: Record<ProductStatus, string> = {
  live: "bg-success-surface text-success",
  paused: "bg-warning-surface text-warning-text",
  draft: "bg-surface-sunken text-black",
  closed: "bg-surface-sunken text-muted",
};

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; products: readonly Product[] };

export function ProductsScreen({ api }: { api: CatalogueApi }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [selected, setSelected] = useState<string | null>(null);

  const fetchProducts = useCallback(
    (): Promise<Load> =>
      api.listProducts().then(
        (products) => ({ state: "ready", products }),
        (error: unknown) => ({ state: "failed", message: catalogueErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchProducts().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchProducts]);

  if (load.state === "loading") {
    return (
      <>
        <PageHeader title="Products" summary={SUMMARY} />
        <p role="status" className="m-0 text-md text-muted">
          Loading products
        </p>
      </>
    );
  }
  if (load.state === "failed") {
    return (
      <>
        <PageHeader title="Products" summary={SUMMARY} />
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Products could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchProducts().then(setLoad)}>
            Try again
          </Button>
        </section>
      </>
    );
  }

  const { products } = load;
  const shown = products.find((product) => product.code === selected) ?? products[0];
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader title="Products" summary={SUMMARY} />
        <Link
          to="/rates"
          className="inline-flex h-11 items-center justify-center rounded-pill bg-black px-[18px] text-sm font-bold text-white hover:bg-ink"
        >
          Change a rate
        </Link>
      </div>
      {products.length === 0 ? (
        <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-[17px] font-extrabold">No products yet</h2>
          <p className="m-0 text-md leading-normal text-muted">Products are added by a migration, with their first rate.</p>
        </section>
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          <div className="min-w-0 flex-[3_1_520px] overflow-x-auto rounded-xl border border-border bg-surface">
            <table className="w-full min-w-[640px] border-collapse text-md">
              <caption className="sr-only">Products</caption>
              <thead>
                <tr className="text-left text-xs text-muted">
                  {["Product", "Rate before tax", "Tax", "Clients see", "Minimum", "Status"].map((heading) => (
                    <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {products.map((product) => (
                  <tr
                    key={product.code}
                    aria-selected={product.code === shown?.code}
                    className={cn("border-t border-divider tabular-nums", product.code === shown?.code && "bg-surface-sunken")}
                  >
                    <th scope="row" className="px-3 py-3 text-left font-bold">
                      <button
                        type="button"
                        className="cursor-pointer text-left font-bold underline-offset-2 hover:underline"
                        onClick={() => setSelected(product.code)}
                      >
                        {product.name}
                      </button>
                      <div className="text-xs font-medium text-muted">
                        {product.code} · {formatTerm(product.termDays)}
                      </div>
                    </th>
                    <td className="px-3 py-3">{product.rate ? formatBps(product.rate.annualRateBps) : "None yet"}</td>
                    <td className="px-3 py-3">{product.rate ? formatBps(product.rate.taxBps) : "None yet"}</td>
                    <td className="px-3 py-3 font-bold">
                      {product.rate ? `${product.rate.clientRatePercent}%` : "None yet"}
                      {product.nextRate ? (
                        <div className="text-xs font-medium text-muted">
                          {product.nextRate.clientRatePercent}% from {formatDay(product.nextRate.effectiveFrom)}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-3 py-3">{formatExact(product.minimum)}</td>
                    <td className="px-3 py-3">
                      <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold whitespace-nowrap", STATUS_STYLES[product.status])}>
                        {PRODUCT_STATUS_LABELS[product.status]}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {shown ? <ClientView product={shown} /> : null}
        </div>
      )}
    </>
  );
}

/** The product as a client sees it: the rate after tax and the terms, never the tax. */
function ClientView({ product }: { product: Product }) {
  return (
    <section aria-labelledby="client-view" className="flex min-w-0 flex-[2_1_300px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <h2 id="client-view" className="m-0 text-[17px] font-extrabold">
        {product.name}
      </h2>
      <dl className="m-0 flex flex-col gap-2.5">
        <Row name="Kind" value={KIND_LABELS[product.kind]} />
        <Row name="Term" value={formatTerm(product.termDays)} />
        <Row name="Interest paid" value={INTEREST_PAID_LABELS[product.interestPaid]} />
        <Row name="Minimum" value={formatExact(product.minimum)} />
        <Row name="Rate in force since" value={product.rate ? formatDay(product.rate.effectiveFrom) : "No rate yet"} />
      </dl>
      <div className="flex flex-col gap-1 rounded-lg bg-surface-sunken p-4">
        <p className="m-0 text-xs font-bold text-muted">What a client sees</p>
        <p className="m-0 text-[22px] font-extrabold tracking-[-0.4px] tabular-nums">
          {product.rate ? `${product.rate.clientRatePercent}% a year` : "Not open yet"}
        </p>
        <p className="m-0 text-sm leading-[1.45] text-muted">
          After tax. Clients never see the rate before tax or the tax itself.
          {product.status === "paused" ? " The product is paused, so clients can see it but not invest." : ""}
        </p>
      </div>
      <p className="m-0 text-sm leading-[1.45] text-muted">
        A rate change applies to new investments only. Running investments keep the rate they were quoted.
      </p>
    </section>
  );
}

function Row({ name, value }: { name: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
      <dt className="text-muted">{name}</dt>
      <dd className="m-0 text-right font-bold tabular-nums">{value}</dd>
    </div>
  );
}

const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

export default function ProductsPage() {
  const { session } = useAuth();
  // Sample products under `pnpm dev` or a preview build. Otherwise nothing until the API client exists.
  const [api] = useState<CatalogueApi>(() =>
    isStubAuth() && session ? createStubCatalogueApi({ me: session.staffId, today: kampalaToday() }) : notConnectedCatalogueApi,
  );
  return <ProductsScreen api={api} />;
}
