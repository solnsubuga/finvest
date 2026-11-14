import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { createHttpDirectory } from "@/directory/http.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { ClientsError, clientsErrorMessage, notConnectedClientsApi, type ClientPage, type ClientsApi, type Decision, type Lock, type LockKind } from "./api.ts";
import { createHttpClientsApi } from "./http.ts";
import { ClientDetails, ClientHeader, INPUT, LockSection } from "./Parts.tsx";
import { PhoneSection } from "./Phone.tsx";
import { createHttpPhoneChangesApi, createStubPhoneChangesApi, notConnectedPhoneChangesApi, type PhoneChangesApi } from "./phone.ts";
import { createStubClientsApi } from "./stub.ts";
import { ClientEmailStatus, SuppressionsSection } from "./Suppressions.tsx";
import { createHttpSuppressionsApi, notConnectedSuppressionsApi, type SuppressionsApi } from "./suppressions.ts";
import { createStubSuppressionsApi } from "./suppressionsStub.ts";

const SUMMARY = "Every client, their accounts and their history.";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; page: ClientPage };
type Search = { state: "idle" } | { state: "searching" } | { state: "none"; query: string } | { state: "failed"; message: string };

interface ClientsScreenProps {
  api: ClientsApi;
  suppressions: SuppressionsApi;
  /** `email_suppressions.lift`. The API checks it again. */
  canLift: boolean;
  /** Null without `client_phones.change`: the mobile number section is not shown. */
  phones?: PhoneChangesApi | null;
}

export function ClientsScreen({ api, suppressions, canLift, phones = null }: ClientsScreenProps) {
  const [params, setParams] = useSearchParams();
  const clientId = params.get("client");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<Search>({ state: "idle" });
  const inputId = useId();

  async function find(event: FormEvent) {
    event.preventDefault();
    const text = query.trim();
    if (!text) return;
    setSearch({ state: "searching" });
    try {
      const [found] = await api.search(text);
      if (found) {
        setSearch({ state: "idle" });
        setParams({ client: found.id });
      } else {
        setSearch({ state: "none", query: text });
      }
    } catch (error) {
      setSearch({ state: "failed", message: clientsErrorMessage(error) });
    }
  }

  return (
    <>
      <PageHeader title="Clients" summary={SUMMARY} />
      <form role="search" aria-label="Client search" onSubmit={(event) => void find(event)} className="flex max-w-[720px] flex-col gap-2">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={inputId} className="text-sm font-bold">
            Find a client
          </label>
          <span id={`${inputId}-hint`} className="text-xs font-medium text-muted">
            By account number, mobile number or client ID.
          </span>
        </div>
        <div className="flex gap-2">
          <input
            id={inputId}
            aria-describedby={`${inputId}-hint`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className={INPUT}
            autoComplete="off"
            spellCheck={false}
          />
          <Button type="submit" disabled={search.state === "searching"} className="shrink-0 self-center">
            {search.state === "searching" ? "Finding" : "Find"}
          </Button>
        </div>
        {search.state === "none" ? (
          <p role="status" className="m-0 text-sm text-muted">
            No client has the account number, mobile number or ID “{search.query}”.
          </p>
        ) : null}
        {search.state === "failed" ? (
          <p role="alert" className="m-0 text-sm font-bold text-danger">
            {search.message}
          </p>
        ) : null}
      </form>
      {clientId ? (
        <ClientView key={clientId} api={api} clientId={clientId} suppressions={suppressions} canLift={canLift} phones={phones} />
      ) : (
        <>
          <p className="m-0 max-w-[720px] rounded-xl border border-border bg-surface p-5 text-md text-muted">
            Find a client to see their account, what Finvest holds for them, where their payouts go, and to lock or unlock them.
          </p>
          <SuppressionsSection api={suppressions} canLift={canLift} />
        </>
      )}
    </>
  );
}

function ClientView({
  api,
  clientId,
  suppressions,
  canLift,
  phones,
}: {
  api: ClientsApi;
  clientId: string;
  suppressions: SuppressionsApi;
  canLift: boolean;
  phones: PhoneChangesApi | null;
}) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchPage = useCallback(
    (): Promise<Load> =>
      api.client(clientId).then(
        (page) => ({ state: "ready", page }),
        (failure: unknown) => ({
          state: "failed",
          message: failure instanceof ClientsError && failure.code === "not_found" ? "There is no client with that ID." : clientsErrorMessage(failure),
        }),
      ),
    [api, clientId],
  );

  useEffect(() => {
    let current = true;
    void fetchPage().then((next) => {
      if (current) setLoad(next);
    });
    return () => {
      current = false;
    };
  }, [fetchPage]);

  async function run(command: () => Promise<Lock>, done: (lock: Lock) => string): Promise<boolean> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const lock = await command();
      const fresh = await fetchPage();
      if (fresh.state === "ready") setLoad(fresh);
      setNotice(done(lock));
      return true;
    } catch (failure) {
      setError(clientsErrorMessage(failure));
      if (failure instanceof ClientsError && ["waiting", "not_found", "already_locked", "not_locked", "closed"].includes(failure.code)) {
        const fresh = await fetchPage();
        if (fresh.state === "ready") setLoad(fresh);
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (load.state === "loading") {
    return (
      <p role="status" className="m-0 text-md text-muted">
        Loading the client
      </p>
    );
  }
  if (load.state === "failed") {
    return (
      <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
        <h2 className="m-0 text-xl font-extrabold">The client could not be loaded</h2>
        <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
        <Button
          variant="secondary"
          onClick={() => {
            setLoad({ state: "loading" });
            void fetchPage().then(setLoad);
          }}
        >
          Try again
        </Button>
      </section>
    );
  }

  const { page } = load;
  const propose = (kind: LockKind, reason: string) =>
    run(
      () => api.propose(page.account.id, kind, reason),
      () => `Sent. Someone else must approve the ${kind} before anything changes.`,
    );
  const decide = (lock: Lock, decision: Decision, reason: string) =>
    run(
      () => api.decide(lock.id, decision, reason),
      () =>
        decision === "approved"
          ? lock.kind === "lock"
            ? "Approved. The account is locked and the client has been signed out everywhere."
            : "Approved. The account is unlocked and the client can sign in again."
          : decision === "rejected"
            ? "Rejected. Nothing changed."
            : "Withdrawn.",
    );

  return (
    <>
      {notice ? (
        <p role="status" className="m-0 max-w-[720px] rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
          {notice}
        </p>
      ) : null}
      <ClientHeader page={page} />
      {page.account.email ? <ClientEmailStatus api={suppressions} email={page.account.email} canLift={canLift} /> : null}
      <ClientDetails page={page} />
      {phones && page.account.status !== "closed" ? (
        <PhoneSection
          api={phones}
          clientId={page.account.id}
          currentPhone={page.account.phone}
          onChanged={() => void fetchPage().then((fresh) => fresh.state === "ready" && setLoad(fresh))}
        />
      ) : null}
      <LockSection page={page} busy={busy} error={error} onPropose={propose} onDecide={decide} />
    </>
  );
}

export default function ClientsPage() {
  const { session } = useAuth();
  const canLift = session ? can(session, "email_suppressions.lift") : false;
  const canChangePhones = session ? can(session, "client_phones.change") : false;
  const [{ api, suppressions, phones }] = useState<{ api: ClientsApi; suppressions: SuppressionsApi; phones: PhoneChangesApi | null }>(() => {
    if (isStubAuth() && session) {
      // Under the stub, `?sample=offline` shows the screen as if Finvest could not be reached.
      if (new URLSearchParams(window.location.search).get("sample") === "offline") {
        return { api: notConnectedClientsApi, suppressions: notConnectedSuppressionsApi, phones: canChangePhones ? notConnectedPhoneChangesApi : null };
      }
      const me = { id: session.staffId, name: session.name };
      return {
        api: createStubClientsApi(me, Date.now, {
          canLock: can(session, "clients.lock"),
          seesSupport: can(session, "support.view"),
          seesComplaints: can(session, "complaints.view"),
        }),
        suppressions: createStubSuppressionsApi(me, Date.now, canLift),
        phones: canChangePhones ? createStubPhoneChangesApi(me) : null,
      };
    }
    const base = apiBaseUrl();
    if (!base) return { api: notConnectedClientsApi, suppressions: notConnectedSuppressionsApi, phones: null };
    const http = createHttp(base);
    return {
      api: createHttpClientsApi(http),
      suppressions: createHttpSuppressionsApi(http),
      phones: canChangePhones && session ? createHttpPhoneChangesApi(http, createHttpDirectory(http), session.staffId) : null,
    };
  });
  return <ClientsScreen api={api} suppressions={suppressions} canLift={canLift} phones={phones} />;
}
