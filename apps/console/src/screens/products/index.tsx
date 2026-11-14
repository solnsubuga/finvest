import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { CatalogueError, catalogueErrorMessage, type CatalogueApi, type Product, type ProductChange, type ProductStatus } from "./api.ts";
import { ChangeCard } from "./ChangeCard.tsx";
import {
  formatBps,
  formatDay,
  formatExact,
  formatTerm,
  INTEREST_PAID_LABELS,
  KIND_LABELS,
  PRODUCT_STATUS_LABELS,
} from "./format.ts";
import { connectedCatalogueApi } from "./http.ts";
import { EditProductForm, NewProductForm } from "./ProductForm.tsx";
import { createStubCatalogueApi, sampleFrom } from "./stub.ts";

const SUMMARY = "What clients can invest in, at what rate, and what they see after tax.";

const STATUS_STYLES: Record<ProductStatus, string> = {
  live: "bg-success-surface text-success",
  paused: "bg-warning-surface text-warning-text",
  draft: "bg-surface-sunken text-black",
  closed: "bg-surface-sunken text-muted",
};

interface Book {
  products: readonly Product[];
  waiting: readonly ProductChange[];
}

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; book: Book };

interface ProductsScreenProps {
  api: CatalogueApi;
  /** The signed-in person's staff ID, to tell their own changes from a colleague's. */
  me?: string;
  /** Whether the person holds `products.change`. Without it the screen only shows products. */
  canChange?: boolean;
}

/**
 * The catalogue, and with `products.change` new products and edits sent for a second person.
 * `?new` opens the New product form and `?edit=CODE` the Edit form for one product.
 */
export function ProductsScreen({ api, me = "", canChange = false }: ProductsScreenProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [round, setRound] = useState(0);
  const [params] = useSearchParams();
  const navigate = useNavigate();

  const fetchBook = useCallback(async (): Promise<Load> => {
    try {
      const [products, waiting] = await Promise.all([api.listProducts(), api.listProductChanges("proposed")]);
      return { state: "ready", book: { products, waiting } };
    } catch (error) {
      return { state: "failed", message: catalogueErrorMessage(error) };
    }
  }, [api]);

  useEffect(() => {
    let current = true;
    void fetchBook().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchBook]);

  async function run(on: string, command: () => Promise<unknown>, after?: () => void): Promise<void> {
    setBusy(on);
    setFailed(null);
    try {
      await command();
      setLoad(await fetchBook());
      setRound((count) => count + 1);
      after?.();
    } catch (error) {
      setFailed({ on, message: catalogueErrorMessage(error) });
      if (error instanceof CatalogueError && (error.code === "conflict" || error.code === "not_found")) {
        const fresh = await fetchBook();
        if (fresh.state === "ready") setLoad(fresh);
      }
    } finally {
      setBusy(null);
    }
  }

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
          <Button variant="secondary" onClick={() => void fetchBook().then(setLoad)}>
            Try again
          </Button>
        </section>
      </>
    );
  }

  const { products, waiting } = load.book;
  const card = (change: ProductChange) => (
    <ChangeCard
      key={`${change.id}:${round}`}
      api={api}
      change={change}
      current={change.action === "create" ? null : (products.find((product) => product.code === change.productCode) ?? null)}
      mine={change.proposedBy === me}
      canDecide={canChange}
      busy={busy === change.id}
      error={failed?.on === change.id ? failed.message : null}
      onApprove={() => void run(change.id, () => api.approveChange(change.id))}
      onSendBack={(note) => void run(change.id, () => api.sendBackChange(change.id, note))}
      onWithdraw={() => void run(change.id, () => api.withdrawChange(change.id))}
    />
  );

  const editing = params.get("edit");
  if (canChange && params.has("new")) {
    return (
      <>
        <Back />
        <PageHeader title="New product" summary="One person proposes a product and a second person approves it. It starts as a draft." />
        <NewProductForm
          busy={busy === "new"}
          error={failed?.on === "new" ? failed.message : null}
          onSubmit={(draft) => void run("new", () => api.proposeNewProduct(draft), () => void navigate("/products"))}
        />
      </>
    );
  }
  const product = editing ? products.find((p) => p.code === editing) : undefined;
  if (canChange && editing) {
    if (!product) {
      return (
        <>
          <Back />
          <PageHeader title="Edit product" summary={SUMMARY} />
          <p role="alert" className="m-0 text-md text-muted">
            There is no product with the code {editing}.
          </p>
        </>
      );
    }
    const open = waiting.find((change) => change.productCode === product.code);
    return (
      <>
        <Back />
        <PageHeader title="Edit product" summary="New terms, pausing and opening each need a second person to approve." />
        {open ? (
          <div className="flex max-w-[640px] flex-col gap-3">
            <p className="m-0 text-md font-bold">A change to this product is waiting for a second person. Nothing else can be proposed until it is decided.</p>
            {card(open)}
          </div>
        ) : null}
        <EditProductForm
          key={`${product.code}:${round}`}
          api={api}
          product={product}
          waiting={open !== undefined}
          busy={busy === "terms" || busy === "status" ? busy : null}
          error={failed?.on === "terms" || failed?.on === "status" ? failed : null}
          onTerms={(draft) => void run("terms", () => api.proposeNewTerms(draft))}
          onStatus={(to, reason) => void run("status", () => api.proposeStatus(product.code, to, reason))}
        />
      </>
    );
  }

  const shown = products.find((p) => p.code === selected) ?? products[0];
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader title="Products" summary={SUMMARY} />
        <div className="flex flex-wrap gap-2">
          {canChange ? (
            <Link
              to="/products?new"
              className="inline-flex h-11 items-center justify-center rounded-pill bg-lime px-[18px] text-sm font-bold text-ink"
            >
              New product
            </Link>
          ) : null}
          <Link
            to="/rates"
            className="inline-flex h-11 items-center justify-center rounded-pill bg-black px-[18px] text-sm font-bold text-white hover:bg-ink"
          >
            Change a rate
          </Link>
        </div>
      </div>
      {waiting.length > 0 ? (
        <section aria-labelledby="changes-waiting" className="flex flex-col gap-3">
          <h2 id="changes-waiting" className="m-0 text-[17px] font-extrabold">
            Waiting for approval
          </h2>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,340px),1fr))] items-start gap-4">{waiting.map(card)}</div>
        </section>
      ) : null}
      {products.length === 0 ? (
        <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-[17px] font-extrabold">No products yet</h2>
          <p className="m-0 text-md leading-normal text-muted">
            {canChange ? "Add the first one with New product. A second person approves it." : "Staff who can change products add them, with a second person approving."}
          </p>
        </section>
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          <div className="min-w-0 flex-[3_1_520px] overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="Products">
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
          {shown ? <ClientView product={shown} canChange={canChange} /> : null}
        </div>
      )}
    </>
  );
}

function Back() {
  return (
    <Link to="/products" className="self-start text-sm font-bold underline underline-offset-2">
      Back to products
    </Link>
  );
}

/** The product as a client sees it: the rate after tax and the terms, never the tax. */
function ClientView({ product, canChange }: { product: Product; canChange: boolean }) {
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
          {product.status === "draft" ? " It is a draft, so clients and the website do not see it yet." : ""}
        </p>
      </div>
      <p className="m-0 text-sm leading-[1.45] text-muted">
        A rate change applies to new investments only. Running investments keep the rate they were quoted.
      </p>
      {canChange ? (
        <Link
          to={`/products?edit=${encodeURIComponent(product.code)}`}
          className="inline-flex h-11 items-center justify-center rounded-pill border border-border-strong bg-surface px-4 text-sm font-bold text-ink hover:bg-surface-sunken"
        >
          Edit {product.name}
        </Link>
      ) : null}
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
  // Sample products under `pnpm dev` or a preview build, like sign-in. Otherwise the real API.
  const [api] = useState<CatalogueApi>(() =>
    isStubAuth() && session
      ? createStubCatalogueApi({
          me: session.staffId,
          today: kampalaToday(),
          sample: sampleFrom(window.location.search),
        })
      : connectedCatalogueApi(),
  );
  return <ProductsScreen api={api} me={session?.staffId ?? ""} canChange={session ? can(session, "products.change") : false} />;
}
