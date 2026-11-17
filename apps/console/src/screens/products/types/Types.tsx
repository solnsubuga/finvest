import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { formatBps, formatStamp, parsePercent } from "../format.ts";
import { Field } from "../ProductForm.tsx";
import { proposedBy } from "../proposer.ts";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { useNames } from "@/screens/review-queue/names.ts";
import {
  MODEL_LABELS,
  ProductTypesError,
  productTypesErrorMessage,
  TYPE_ACTION_LABELS,
  TYPE_STATUS_LABELS,
  type NewTypeDraft,
  type ProductType,
  type ProductTypeBook,
  type ProductTypeChange,
  type ProductTypesApi,
  type ReturnModel,
  type TypeStatus,
  type TypeTerms,
} from "./api.ts";

const CODE = /^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$/;
const TEXTAREA = "w-full rounded-lg border border-border-strong bg-surface p-3 text-md focus-visible:border-black aria-invalid:border-danger";
const STATUS_STYLES: Record<TypeStatus, string> = {
  active: "bg-success-surface text-success",
  retired: "bg-surface-sunken text-muted",
};

interface Book extends ProductTypeBook {
  waiting: readonly ProductTypeChange[];
}

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; book: Book };

/** The types and the changes waiting, loaded together, with a command runner that reloads both. */
function useTypeBook(api: ProductTypesApi) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [round, setRound] = useState(0);

  const fetchBook = useCallback(async (): Promise<Load> => {
    try {
      const [book, waiting] = await Promise.all([api.listTypes(), api.listChanges("proposed")]);
      return { state: "ready", book: { ...book, waiting } };
    } catch (error) {
      return { state: "failed", message: productTypesErrorMessage(error) };
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
      setFailed({ on, message: productTypesErrorMessage(error) });
      if (error instanceof ProductTypesError && (error.code === "conflict" || error.code === "not_found")) {
        const fresh = await fetchBook();
        if (fresh.state === "ready") setLoad(fresh);
      }
    } finally {
      setBusy(null);
    }
  }

  const retry = () => void fetchBook().then(setLoad);
  return { load, busy, failed, round, run, retry };
}

interface TypesSectionProps {
  api: ProductTypesApi;
  me: string;
  /** Names the colleague who proposed each change. */
  directory?: Directory;
  canChange: boolean;
}

/** The product types on the Products screen, with changes waiting for a second person. */
export function TypesSection({ api, me, directory = emptyDirectory, canChange }: TypesSectionProps) {
  const { load, busy, failed, round, run, retry } = useTypeBook(api);
  const { staff } = useNames(directory, [], load.state === "ready" ? load.book.waiting.map((change) => change.proposedBy) : []);

  const heading = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-col gap-1">
        <h2 id="product-types" className="m-0 text-xl font-extrabold">
          Product types
        </h2>
        <p className="m-0 max-w-[680px] text-md leading-normal text-muted">
          What staff choose from when adding a product. Each type earns in one of the ways Finvest supports, so a new type needs no code, only a second person to
          approve it.
        </p>
      </div>
      {canChange ? (
        <Link to="/products?new-type" className="inline-flex h-11 items-center justify-center rounded-pill bg-black px-[18px] text-sm font-bold text-white hover:bg-ink">
          New product type
        </Link>
      ) : null}
    </div>
  );

  if (load.state !== "ready") {
    return (
      <section aria-labelledby="product-types" className="flex flex-col gap-4">
        {heading}
        {load.state === "loading" ? (
          <p role="status" className="m-0 text-md text-muted">
            Loading product types
          </p>
        ) : (
          <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
            <h3 className="m-0 text-lg font-extrabold">Product types could not be loaded</h3>
            <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
            <Button variant="secondary" onClick={retry}>
              Try again
            </Button>
          </div>
        )}
      </section>
    );
  }

  const { types, waiting } = load.book;
  return (
    <section aria-labelledby="product-types" className="flex flex-col gap-4">
      {heading}
      {waiting.length > 0 ? (
        <div className="flex flex-col gap-3">
          <h3 className="m-0 text-[17px] font-extrabold">Type changes waiting for approval</h3>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,340px),1fr))] items-start gap-4">
            {waiting.map((change) => (
              <TypeChangeCard
                key={`${change.id}:${round}`}
                change={change}
                current={types.find((t) => t.code === change.code) ?? null}
                mine={change.proposedBy === me}
                proposerName={staff[change.proposedBy]}
                canDecide={canChange}
                busy={busy === change.id}
                error={failed?.on === change.id ? failed.message : null}
                onApprove={() => void run(change.id, () => api.approve(change.id))}
                onSendBack={(note) => void run(change.id, () => api.sendBack(change.id, note))}
                onWithdraw={() => void run(change.id, () => api.withdraw(change.id))}
              />
            ))}
          </div>
        </div>
      ) : null}
      <div className="min-w-0 overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="Product types">
        <table className="w-full min-w-[720px] border-collapse text-md">
          <caption className="sr-only">Product types</caption>
          <thead>
            <tr className="text-left text-xs text-muted">
              {["Type", "How it earns", "Usual tax", "Products", "Status", ...(canChange ? [""] : [])].map((name, index) => (
                <th key={name || index} scope="col" className="px-3 py-3.5 font-bold">
                  {name ? name : <span className="sr-only">Change</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {types.map((type) => (
              <tr key={type.code} className="border-t border-divider align-top tabular-nums">
                <th scope="row" className="max-w-[320px] px-3 py-3 text-left font-bold">
                  {type.name}
                  <div className="text-xs font-medium text-muted">
                    {type.code}
                    {type.builtIn ? " · Built in" : ""}
                  </div>
                  <p className="m-0 mt-1 text-sm leading-[1.45] font-medium text-muted">{type.description}</p>
                </th>
                <td className="px-3 py-3">{MODEL_LABELS[type.model]}</td>
                <td className="px-3 py-3">{formatBps(type.defaultTaxBps)}</td>
                <td className="px-3 py-3">{type.inUse}</td>
                <td className="px-3 py-3">
                  <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold whitespace-nowrap", STATUS_STYLES[type.status])}>{TYPE_STATUS_LABELS[type.status]}</span>
                </td>
                {canChange ? (
                  <td className="px-3 py-3 text-right">
                    <Link to={`/products?edit-type=${encodeURIComponent(type.code)}`} className="text-sm font-bold whitespace-nowrap underline underline-offset-2">
                      Change {type.name}
                    </Link>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

interface TypeChangeCardProps {
  change: ProductTypeChange;
  /** The type as it is now. Null for a new type. */
  current: ProductType | null;
  mine: boolean;
  proposerName?: string | null | undefined;
  canDecide: boolean;
  busy: boolean;
  error: string | null;
  onApprove(): void;
  onSendBack(note: string): void;
  onWithdraw(): void;
}

interface Line {
  name: string;
  before: string | null;
  after: string;
}

function lines(change: ProductTypeChange, current: ProductType | null): Line[] {
  const t = change.terms;
  if (change.action === "create" && t) {
    return [
      { name: "Code", before: null, after: change.code },
      { name: "How it earns", before: null, after: MODEL_LABELS[change.model] },
      { name: "Usual tax", before: null, after: formatBps(t.defaultTaxBps) },
    ];
  }
  if (change.action === "edit" && t && current) {
    return [
      { name: "Name", before: current.name, after: t.name },
      { name: "Usual tax", before: formatBps(current.defaultTaxBps), after: formatBps(t.defaultTaxBps) },
    ].filter((line) => line.before !== line.after);
  }
  return [{ name: "Status", before: current ? TYPE_STATUS_LABELS[current.status] : null, after: change.action === "retire" ? "Retired" : "In use" }];
}

/** A type change waiting for a second person, with what it changes. */
export function TypeChangeCard({ change, current, mine, proposerName, canDecide, busy, error, onApprove, onSendBack, onWithdraw }: TypeChangeCardProps) {
  const [sendingBack, setSendingBack] = useState(false);
  const [note, setNote] = useState("");
  const noteId = `type-change-note-${change.id}`;
  const title = change.action === "create" ? (change.terms?.name ?? change.code) : (current?.name ?? change.code);
  const newDescription = change.terms && change.terms.description !== current?.description ? change.terms.description : null;
  return (
    <section
      aria-labelledby={`type-change-${change.id}`}
      className={cn("flex flex-col gap-3 rounded-xl bg-surface p-5", mine || !canDecide ? "border border-border" : "border-2 border-warning p-[19px]")}
    >
      <p className="m-0 text-xs font-bold text-muted">
        {TYPE_ACTION_LABELS[change.action]} · {mine ? "waiting for a second person" : canDecide ? "waiting for you" : "waiting for approval"}
      </p>
      <h3 id={`type-change-${change.id}`} className="m-0 text-[17px] font-extrabold">
        {title}
      </h3>
      <dl className="m-0 flex flex-col gap-2.5">
        {lines(change, current).map((line) => (
          <div key={line.name} className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
            <dt className="text-muted">{line.name}</dt>
            <dd className="m-0 text-right font-bold tabular-nums">{line.before ? `${line.before} to ${line.after}` : line.after}</dd>
          </div>
        ))}
        {newDescription ? (
          <div className="flex flex-col gap-1 border-t border-divider pt-2.5 text-md">
            <dt className="text-muted">{change.action === "create" ? "Description" : "New description"}</dt>
            <dd className="m-0 leading-[1.45]">{newDescription}</dd>
          </div>
        ) : null}
        <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
          <dt className="text-muted">Proposed</dt>
          <dd className="m-0 text-right font-bold tabular-nums">{`${proposedBy(mine, proposerName)}, ${formatStamp(change.proposedAt)}`}</dd>
        </div>
      </dl>
      <p className="m-0 rounded-lg bg-surface-sunken p-3 text-sm leading-[1.45]">{change.reason}</p>
      <p className="m-0 text-sm leading-[1.45] text-muted">
        {change.action === "create"
          ? "Once approved, staff can choose it when adding a product. Nothing is offered to clients until a product of it is approved and opened."
          : change.action === "retire"
            ? "No new products of it can be added. Products that exist carry on as they are."
            : change.action === "restore"
              ? "Staff can choose it again when adding a product."
              : "Products of this type keep their own names and taxes. The usual tax is only what a new product starts with."}
      </p>
      {error ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}
      {!canDecide ? null : mine ? (
        <Button variant="secondary" disabled={busy} onClick={onWithdraw}>
          Withdraw this change
        </Button>
      ) : sendingBack ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (note.trim()) onSendBack(note.trim());
          }}
        >
          <Label htmlFor={noteId}>Why it is going back</Label>
          <textarea id={noteId} value={note} onChange={(event) => setNote(event.target.value)} rows={3} className={TEXTAREA} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !note.trim()}>
              Send back
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} onClick={onApprove}>
            Approve as second person
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(true)}>
            Send back with a note
          </Button>
        </div>
      )}
    </section>
  );
}

function Back() {
  return (
    <Link to="/products" className="self-start text-sm font-bold underline underline-offset-2">
      Back to products
    </Link>
  );
}

interface TypePageProps {
  api: ProductTypesApi;
  me: string;
  directory?: Directory;
  /** Null for a new type, otherwise the code of the type to change. */
  code: string | null;
}

/** `?new-type` defines a new product type; `?edit-type=CODE` changes, retires or restores one. */
export function TypePage({ api, me, directory = emptyDirectory, code }: TypePageProps) {
  const { load, busy, failed, round, run, retry } = useTypeBook(api);
  const { staff } = useNames(directory, [], load.state === "ready" ? load.book.waiting.map((change) => change.proposedBy) : []);
  const navigate = useNavigate();
  const title = code === null ? "New product type" : "Change product type";
  const summary =
    code === null
      ? "Choose how products of the new type earn, then name it. A second person approves it, and it then appears in the Type list on New product."
      : "A new name, description or usual tax, and retiring or restoring, each need a second person to approve.";

  if (load.state !== "ready") {
    return (
      <>
        <Back />
        <PageHeader title={title} summary={summary} />
        {load.state === "loading" ? (
          <p role="status" className="m-0 text-md text-muted">
            Loading product types
          </p>
        ) : (
          <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
            <h2 className="m-0 text-xl font-extrabold">Product types could not be loaded</h2>
            <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
            <Button variant="secondary" onClick={retry}>
              Try again
            </Button>
          </section>
        )}
      </>
    );
  }

  const { types, models, waiting } = load.book;
  if (code === null) {
    return (
      <>
        <Back />
        <PageHeader title={title} summary={summary} />
        <NewTypeForm
          models={models}
          busy={busy === "new-type"}
          error={failed?.on === "new-type" ? failed.message : null}
          onSubmit={(draft) => void run("new-type", () => api.proposeType(draft), () => void navigate("/products"))}
        />
      </>
    );
  }

  const type = types.find((t) => t.code === code);
  if (!type) {
    return (
      <>
        <Back />
        <PageHeader title={title} summary={summary} />
        <p role="alert" className="m-0 text-md text-muted">
          There is no product type with the code {code}.
        </p>
      </>
    );
  }
  const open = waiting.find((change) => change.code === type.code);
  return (
    <>
      <Back />
      <PageHeader title={title} summary={summary} />
      {open ? (
        <div className="flex max-w-[640px] flex-col gap-3">
          <p className="m-0 text-md font-bold">A change to this type is waiting for a second person. Nothing else can be proposed until it is decided.</p>
          <TypeChangeCard
            key={`${open.id}:${round}`}
            change={open}
            current={type}
            mine={open.proposedBy === me}
            proposerName={staff[open.proposedBy]}
            canDecide
            busy={busy === open.id}
            error={failed?.on === open.id ? failed.message : null}
            onApprove={() => void run(open.id, () => api.approve(open.id))}
            onSendBack={(note) => void run(open.id, () => api.sendBack(open.id, note))}
            onWithdraw={() => void run(open.id, () => api.withdraw(open.id))}
          />
        </div>
      ) : null}
      <EditTypeForm
        key={`${type.code}:${round}`}
        type={type}
        waiting={open !== undefined}
        busy={busy === "edit" || busy === "status" ? busy : null}
        error={failed?.on === "edit" || failed?.on === "status" ? failed : null}
        onEdit={(terms, reason) => void run("edit", () => api.proposeEdit(type.code, terms, reason))}
        onStatus={(to, reason) => void run("status", () => api.proposeStatus(type.code, to, reason))}
      />
    </>
  );
}

/** Name, description and usual tax, which a new type and an edit share. */
function useTermsFields(start: TypeTerms | null) {
  const [name, setName] = useState(start?.name ?? "");
  const [description, setDescription] = useState(start?.description ?? "");
  const [taxText, setTaxText] = useState(start ? formatBps(start.defaultTaxBps).replace("%", "") : "15");
  const taxBps = parsePercent(taxText);
  const problems = {
    name: name.trim() && name.trim().length <= 60 ? null : "Give the type a name of up to 60 characters, such as Company note.",
    description: description.trim() && description.trim().length <= 300 ? null : "Say in up to 300 characters what a product of this type is.",
    tax: taxBps === null ? "Type the usual tax as a percentage, such as 15." : null,
  };
  const terms: TypeTerms | null = taxBps === null ? null : { name: name.trim(), description: description.trim(), defaultTaxBps: taxBps };
  return { name, setName, description, setDescription, taxText, setTaxText, problems, terms };
}

function TermsFields({ fields, checked }: { fields: ReturnType<typeof useTermsFields>; checked: boolean }) {
  const { name, setName, description, setDescription, taxText, setTaxText, problems } = fields;
  return (
    <>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field id="type-name" label="Name" hint="What staff choose in the Type list." problem={checked ? problems.name : null}>
          <Input id="type-name" value={name} onChange={(event) => setName(event.target.value)} aria-invalid={checked && !!problems.name} />
        </Field>
        <Field id="type-tax" label="Usual tax, %" hint="What a new product of this type starts with." problem={checked ? problems.tax : null}>
          <Input id="type-tax" inputMode="decimal" value={taxText} onChange={(event) => setTaxText(event.target.value)} aria-invalid={checked && !!problems.tax} />
        </Field>
      </div>
      <Field id="type-description" label="Description" hint="For staff. Up to 300 characters." problem={checked ? problems.description : null}>
        <textarea
          id="type-description"
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          aria-invalid={checked && !!problems.description}
          className={TEXTAREA}
        />
      </Field>
    </>
  );
}

interface NewTypeFormProps {
  models: readonly { model: ReturnModel; description: string }[];
  busy: boolean;
  error: string | null;
  onSubmit(draft: NewTypeDraft): void;
}

/** A new product type built on one of the return models, sent for a second person to approve. */
export function NewTypeForm({ models, busy, error, onSubmit }: NewTypeFormProps) {
  const [model, setModel] = useState<ReturnModel>(models[0]?.model ?? "fixed_income");
  const [code, setCode] = useState("");
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState(false);
  const fields = useTermsFields(null);
  const reasonId = useId();
  const problems = {
    code: CODE.test(code) && code.length <= 24 ? null : "Use capital letters, digits and hyphens, such as COMPANY-NOTE.",
    reason: reason.trim() ? null : "Say why Finvest is adding this type.",
    ...fields.problems,
  };

  function submit(event: FormEvent) {
    event.preventDefault();
    setChecked(true);
    if (Object.values(problems).some(Boolean) || !fields.terms) return;
    onSubmit({ code, model, ...fields.terms, reason: reason.trim() });
  }

  return (
    <form noValidate aria-labelledby="new-type" onSubmit={submit} className="flex flex-wrap items-start gap-5">
      <section className="flex min-w-0 flex-[3_1_480px] flex-col gap-4 rounded-xl border border-border bg-surface p-5">
        <h2 id="new-type" className="m-0 text-[17px] font-extrabold">
          New product type
        </h2>
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="mb-1.5 text-sm font-bold">How products of it earn</legend>
          {models.map((option) => (
            <label
              key={option.model}
              className={cn(
                "flex cursor-pointer items-start gap-3 rounded-lg border p-3.5",
                option.model === model ? "border-2 border-black p-[13px]" : "border-border-strong",
              )}
            >
              <input type="radio" name="type-model" className="mt-1" checked={option.model === model} onChange={() => setModel(option.model)} />
              <span className="flex flex-col gap-0.5">
                <span className="text-md font-bold">{MODEL_LABELS[option.model]}</span>
                <span className="text-sm leading-[1.45] text-muted">{option.description}</span>
              </span>
            </label>
          ))}
          <p className="m-0 text-xs text-muted">It never changes. A new way of earning, such as a unit trust, needs building first.</p>
        </fieldset>
        <Field id="type-code" label="Code" hint="Staff see it. It never changes." problem={checked ? problems.code : null}>
          <Input id="type-code" value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} aria-invalid={checked && !!problems.code} />
        </Field>
        <TermsFields fields={fields} checked={checked} />
        <Field id={reasonId} label="Reason, for the audit trail" problem={checked ? problems.reason : null}>
          <textarea id={reasonId} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} aria-invalid={checked && !!problems.reason} className={TEXTAREA} />
        </Field>
      </section>
      <aside className="flex min-w-0 flex-[2_1_300px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
        <h2 className="m-0 text-[17px] font-extrabold">What happens next</h2>
        <p className="m-0 text-sm leading-[1.45] text-muted">
          A second person approves the type. It then appears in the Type list on New product, and a product of it uses the form for{" "}
          <span className="font-bold text-ink">{MODEL_LABELS[model].toLowerCase()}</span>.
        </p>
        <p className="m-0 text-sm leading-[1.45] text-muted">Clients see nothing until a product of the type is approved and opened.</p>
        {error ? (
          <p role="alert" className="m-0 text-sm font-bold text-danger">
            {error}
          </p>
        ) : null}
        <Button type="submit" disabled={busy}>
          Send for approval
        </Button>
        <Link to="/products" className="text-center text-sm font-bold underline underline-offset-2">
          Cancel
        </Link>
      </aside>
    </form>
  );
}

interface EditTypeFormProps {
  type: ProductType;
  /** A change to this type already waits for a second person, so no other can be proposed. */
  waiting: boolean;
  busy: string | null;
  error: { on: string; message: string } | null;
  onEdit(terms: TypeTerms, reason: string): void;
  onStatus(to: TypeStatus, reason: string): void;
}

function EditTypeForm({ type, waiting, busy, error, onEdit, onStatus }: EditTypeFormProps) {
  const fields = useTermsFields(type);
  const [reason, setReason] = useState("");
  const [statusReason, setStatusReason] = useState("");
  const [checked, setChecked] = useState(false);
  const [statusChecked, setStatusChecked] = useState(false);
  const reasonProblem = reason.trim() ? null : "Say why. The reason goes in the audit trail.";
  const statusProblem = statusReason.trim() ? null : "Say why. The reason goes in the audit trail.";
  const to: TypeStatus = type.status === "active" ? "retired" : "active";

  function submitEdit(event: FormEvent) {
    event.preventDefault();
    setChecked(true);
    if (Object.values(fields.problems).some(Boolean) || reasonProblem || !fields.terms) return;
    onEdit(fields.terms, reason.trim());
  }

  function submitStatus(event: FormEvent) {
    event.preventDefault();
    setStatusChecked(true);
    if (statusProblem) return;
    onStatus(to, statusReason.trim());
  }

  return (
    <div className="flex flex-wrap items-start gap-5">
      <form noValidate aria-labelledby="edit-type" onSubmit={submitEdit} className="flex min-w-0 flex-[3_1_480px] flex-col gap-4 rounded-xl border border-border bg-surface p-5">
        <h2 id="edit-type" className="m-0 text-[17px] font-extrabold">
          {type.name}
        </h2>
        <dl className="m-0 flex flex-col gap-2.5">
          <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
            <dt className="text-muted">Code</dt>
            <dd className="m-0 text-right font-bold">{type.code}</dd>
          </div>
          <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
            <dt className="text-muted">How it earns</dt>
            <dd className="m-0 text-right font-bold">{MODEL_LABELS[type.model]}</dd>
          </div>
          <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
            <dt className="text-muted">Products of this type</dt>
            <dd className="m-0 text-right font-bold tabular-nums">{type.inUse}</dd>
          </div>
        </dl>
        <TermsFields fields={fields} checked={checked} />
        <Field id="type-reason" label="Reason, for the audit trail" problem={checked ? reasonProblem : null}>
          <textarea id="type-reason" rows={3} value={reason} onChange={(event) => setReason(event.target.value)} aria-invalid={checked && !!reasonProblem} className={TEXTAREA} />
        </Field>
        <p className="m-0 text-sm leading-[1.45] text-muted">
          Products of this type keep their own names and taxes. A new usual tax is only what new products start with.
        </p>
        {error?.on === "edit" ? (
          <p role="alert" className="m-0 text-sm font-bold text-danger">
            {error.message}
          </p>
        ) : null}
        <Button type="submit" className="self-start" disabled={waiting || busy !== null}>
          Send for approval
        </Button>
      </form>
      <form noValidate aria-labelledby="type-status" onSubmit={submitStatus} className="flex min-w-0 flex-[2_1_300px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
        <h2 id="type-status" className="m-0 text-[17px] font-extrabold">
          {to === "retired" ? "Retire this type" : "Restore this type"}
        </h2>
        <p className="m-0 text-sm leading-[1.45] text-muted">
          {to === "retired"
            ? "Nobody can add a new product of it. The products that exist carry on as they are, and clients notice nothing."
            : "Staff can choose it again when adding a product."}
        </p>
        <Field id="type-status-reason" label="Reason, for the audit trail" problem={statusChecked ? statusProblem : null}>
          <textarea
            id="type-status-reason"
            rows={3}
            value={statusReason}
            onChange={(event) => setStatusReason(event.target.value)}
            aria-invalid={statusChecked && !!statusProblem}
            className={TEXTAREA}
          />
        </Field>
        {error?.on === "status" ? (
          <p role="alert" className="m-0 text-sm font-bold text-danger">
            {error.message}
          </p>
        ) : null}
        <Button type="submit" variant="secondary" disabled={waiting || busy !== null}>
          {to === "retired" ? "Send retiring for approval" : "Send restoring for approval"}
        </Button>
      </form>
    </div>
  );
}
