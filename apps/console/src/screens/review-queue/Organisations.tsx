import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import { formatMoment, timeAgo } from "./labels.ts";
import {
  OrganisationsError,
  organisationsErrorMessage,
  type Decision,
  type DocumentKind,
  type DocumentLink,
  type Organisation,
  type OrganisationsApi,
} from "./organisations.ts";
import { Empty, ErrorNote, Failure, FIELD, FieldNote, Loading, Notice, Panel, Pill, QueueButton, Row, Section, type Load } from "./Parts.tsx";

const MAX_NOTE = 500;

const KIND_LABEL = { company: "Company", group: "Group or SACCO" } as const;

export const DOCUMENT_LABEL: Record<DocumentKind, string> = {
  certificate_of_incorporation: "Certificate of incorporation",
  board_resolution: "Board resolution",
  constitution: "Constitution",
  group_resolution: "Group resolution",
};

const SIGNATORY_STATUS = { invited: "Not answered", joined: "Joined", declined: "Declined", removed: "Taken off" } as const;

/** Refusals after which the list on screen is out of date, so it is read again. */
const RELOAD_AFTER = new Set(["already_decided", "not_found"]);

interface OrganisationsProps {
  api: OrganisationsApi;
  now: () => Date;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCount?: (count: number) => void;
}

function stageOf(found: Organisation): string {
  if (found.status === "checked") return found.canApprove ? "Checked, needs a second person" : "Checked by you, needs a second person";
  return "Needs a check";
}

function fileSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function OrganisationsSection({ api, now, selectedId, onSelect, onCount }: OrganisationsProps) {
  const [list, setList] = useState<Load<Organisation[]>>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null);
  // An application just decided stays open, with what happened to it, after it leaves the list.
  const [decided, setDecided] = useState<{ found: Organisation; decision: Decision } | null>(null);

  const fetchList = useCallback(
    (): Promise<Load<Organisation[]>> =>
      api.list().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: organisationsErrorMessage(error) }),
      ),
    [api],
  );
  const reloadList = async () => setList(await fetchList());

  useEffect(() => {
    let current = true;
    void fetchList().then((load) => current && setList(load));
    return () => {
      current = false;
    };
  }, [fetchList]);

  useEffect(() => {
    if (list.state === "ready") onCount?.(list.value.length);
  }, [list, onCount]);

  const items = list.state === "ready" ? list.value : [];
  const shownId = selectedId ?? decided?.found.id ?? items[0]?.id ?? null;
  const listed = items.find((item) => item.id === shownId);
  const shown = decided?.found.id === shownId ? decided.found : listed ?? null;

  async function decide(target: Organisation, decision: Decision, note: string) {
    setBusy(true);
    setFailed(null);
    try {
      const found = await api.decide(target.id, decision, note);
      setDecided({ found, decision });
      await reloadList();
    } catch (error) {
      setFailed({ id: target.id, message: organisationsErrorMessage(error) });
      if (error instanceof OrganisationsError && RELOAD_AFTER.has(error.code)) await reloadList();
    } finally {
      setBusy(false);
    }
  }

  if (list.state === "loading") return <Loading>Loading company and group accounts</Loading>;
  if (list.state === "failed") {
    return <Failure title="Company and group accounts could not be loaded" message={list.message} onRetry={() => void reloadList()} />;
  }
  if (items.length === 0 && !shown) {
    return (
      <Empty title="No applications waiting">
        Nothing is waiting. A company or group account appears here once its applicant hands it in, until one member of staff
        checks it and a different one approves it, or one sends it back or rejects it.
      </Empty>
    );
  }

  return (
    <div className="flex flex-wrap items-start gap-5">
      <div className="min-w-0 flex-[1_1_260px]">
        {items.length === 0 ? (
          <p className="m-0 text-md text-muted">No other applications are waiting.</p>
        ) : (
          <ul aria-label="Company and group accounts" className="m-0 flex list-none flex-col gap-2.5 p-0">
            {items.map((item) => (
              <li key={item.id}>
                <QueueButton
                  selected={item.id === shownId}
                  onSelect={() => {
                    setDecided(null);
                    onSelect(item.id);
                  }}
                >
                  <span className="text-md font-bold">{item.name}</span>
                  <span className="text-xs text-muted">
                    {KIND_LABEL[item.kind]}, handed in {item.submittedAt ? timeAgo(item.submittedAt, now()) : ""}
                  </span>
                  <span className={cn("mt-1 text-xs font-bold", item.status === "checked" && !item.canApprove ? "text-muted" : "text-warning-text")}>
                    {stageOf(item)}
                  </span>
                </QueueButton>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="min-w-0 flex-[3_1_480px]">
        {shown ? (
          <OrganisationReview
            key={`${shown.id}:${shown.status}`}
            found={shown}
            api={api}
            decided={decided?.found.id === shown.id ? decided.decision : null}
            busy={busy}
            error={failed?.id === shown.id ? failed.message : null}
            onDecide={(decision, note) => void decide(shown, decision, note)}
          />
        ) : (
          <Failure title="This application is not waiting" message="It may have been decided already. Choose another from the list." />
        )}
      </div>
    </div>
  );
}

const RESULT: Record<Decision, (found: Organisation) => string> = {
  check: () => "Checked. A different member of staff now approves it, which opens the account.",
  approve: (found) =>
    `Approved. ${found.name} has its own account number, ${found.accountNumber ?? ""}, and every signatory is told. It cannot invest yet.`,
  send_back: () => "Sent back. The applicant sees your note and can change it and hand it in again.",
  reject: () => "Rejected. The applicant is told, and may apply again.",
};

const RESULT_PILL: Record<Decision, { tone: "done" | "refused" | "quiet"; label: string }> = {
  check: { tone: "quiet", label: "Checked" },
  approve: { tone: "done", label: "Open" },
  send_back: { tone: "quiet", label: "Sent back" },
  reject: { tone: "refused", label: "Rejected" },
};

function OrganisationReview({
  found,
  api,
  decided,
  busy,
  error,
  onDecide,
}: {
  found: Organisation;
  api: OrganisationsApi;
  decided: Decision | null;
  busy: boolean;
  error: string | null;
  onDecide: (decision: Decision, note: string) => void;
}) {
  const [links, setLinks] = useState<Load<DocumentLink[]> | null>(null);
  const people = found.signatories.filter((s) => s.status !== "removed");
  const joined = people.filter((s) => s.status === "joined").length;
  const rule = found.approvalsNeeded >= joined ? `All ${joined} must approve` : `Any ${found.approvalsNeeded} of ${joined} must approve`;

  async function openDocuments() {
    setLinks({ state: "loading" });
    try {
      setLinks({ state: "ready", value: await api.documentLinks(found.id) });
    } catch (caught) {
      setLinks({ state: "failed", message: organisationsErrorMessage(caught) });
    }
  }

  const pill = decided ? RESULT_PILL[decided] : { tone: "waiting" as const, label: stageOf(found) };

  return (
    <Panel label={`Application for ${found.name}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <h2 className="m-0 text-2xl font-extrabold">{found.name}</h2>
          <span className="text-sm text-muted">{KIND_LABEL[found.kind]} account</span>
        </div>
        <Pill tone={pill.tone}>{pill.label}</Pill>
      </div>

      <div className="flex flex-wrap gap-5">
        <Section title="The application">
          <Row name="Kind" value={KIND_LABEL[found.kind]} />
          <Row name="Registration number" value={found.registrationNumber ?? "None given"} />
          <Row name="Handed in" value={found.submittedAt ? formatMoment(found.submittedAt) : "Not yet"} />
          <Row name="Money leaving" value={rule} />
          {found.accountNumber ? <Row name="Account number" value={<span className="tabular-nums">{found.accountNumber}</span>} /> : null}
        </Section>
        {found.check ? (
          <Section title="The check">
            <Row name="Checked by" value={`${found.check.byName ?? "A colleague"}, ${formatMoment(found.check.at)}`} />
            <Row name="Note" value={found.check.note ?? ""} />
          </Section>
        ) : null}
      </div>

      <section className="flex flex-col gap-2">
        <h3 className="m-0 text-xs font-bold tracking-[0.6px] text-muted uppercase">Who signs for it</h3>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[560px] border-collapse text-left text-md">
            <thead>
              <tr className="bg-surface-sunken text-xs font-bold tracking-[0.4px] text-muted uppercase">
                <th scope="col" className="px-3 py-2.5">Name on their ID</th>
                <th scope="col" className="px-3 py-2.5">Role</th>
                <th scope="col" className="px-3 py-2.5">Finvest account</th>
                <th scope="col" className="px-3 py-2.5">Status</th>
              </tr>
            </thead>
            <tbody>
              {people.map((person) => (
                <tr key={person.clientId} className="border-t border-divider">
                  <td className="px-3 py-2.5 font-bold">
                    {person.verifiedName ?? <span className="text-danger">No approved ID check</span>}
                    {person.isApplicant ? <span className="ml-2 text-xs font-semibold text-muted">Applied</span> : null}
                  </td>
                  <td className="px-3 py-2.5">{person.role}</td>
                  <td className="px-3 py-2.5 tabular-nums">{person.accountNumber}</td>
                  <td className="px-3 py-2.5">{SIGNATORY_STATUS[person.status]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="flex flex-col gap-2">
        <h3 className="m-0 text-xs font-bold tracking-[0.6px] text-muted uppercase">Documents</h3>
        <ul className="m-0 flex list-none flex-col gap-0 p-0">
          {found.documentsNeeded.map((kind) => {
            const doc = found.documents.find((d) => d.kind === kind);
            const link = links?.state === "ready" ? links.value.find((l) => l.kind === kind) : undefined;
            return (
              <li key={kind} className="flex flex-wrap items-center justify-between gap-2 border-b border-divider py-2.5 text-md">
                <span className="font-bold">{DOCUMENT_LABEL[kind]}</span>
                {link ? (
                  <a href={link.url} target="_blank" rel="noreferrer noopener" className="font-bold text-ink underline">
                    Open {doc?.contentType === "application/pdf" ? "PDF" : "photo"}
                  </a>
                ) : (
                  <span className="text-muted">
                    {doc ? `${doc.contentType === "application/pdf" ? "PDF" : "Photo"}, ${fileSize(doc.byteSize)}` : "Missing"}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
        {links?.state === "failed" ? <ErrorNote>{links.message}</ErrorNote> : null}
        {links?.state === "ready" ? (
          <p className="m-0 text-xs text-muted">The links work for five minutes. Opening them is on the audit trail.</p>
        ) : (
          <div>
            <Button variant="secondary" disabled={links?.state === "loading"} onClick={() => void openDocuments()}>
              {links?.state === "loading" ? "Getting links" : "Get links to the documents"}
            </Button>
          </div>
        )}
      </section>

      <p className="m-0 text-md leading-normal text-muted">
        Check the documents, the registration number against URSB, and that the people named in the resolution are the people
        who sign. One person checks, a different person approves. Either may send it back or reject it.
      </p>

      {decided ? (
        <Notice>{RESULT[decided](found)}</Notice>
      ) : found.canCheck || found.canApprove || found.canSendBack || found.canReject ? (
        <DecisionForm found={found} busy={busy} error={error} onDecide={onDecide} />
      ) : (
        <p className="m-0 border-t border-divider pt-4 text-md leading-normal text-muted">
          You checked this application, so a different member of staff must approve it.
        </p>
      )}
    </Panel>
  );
}

const CONFIRMATIONS: Partial<Record<Decision, { text: string; confirm: string }>> = {
  approve: {
    text: "Approving opens the account and gives it its own account number. It cannot be undone. You confirm it is you next.",
    confirm: "Yes, approve",
  },
  reject: { text: "Rejecting closes this application for good. The applicant may apply again. You confirm it is you next.", confirm: "Yes, reject" },
};

function DecisionForm({
  found,
  busy,
  error,
  onDecide,
}: {
  found: Organisation;
  busy: boolean;
  error: string | null;
  onDecide: (decision: Decision, note: string) => void;
}) {
  const ids = useId();
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | undefined>();
  const [confirming, setConfirming] = useState<Decision | null>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) confirmButton.current?.focus();
  }, [confirming]);

  function attempt(decision: Decision, confirmed = false) {
    if (!note.trim()) {
      setProblem(decision === "send_back" ? "Say what the applicant must change." : "Write a note for the audit trail.");
      setConfirming(null);
      return;
    }
    setProblem(undefined);
    if (!confirmed && CONFIRMATIONS[decision]) {
      setConfirming(decision);
      return;
    }
    setConfirming(null);
    onDecide(decision, note.trim());
  }

  const asked = confirming ? CONFIRMATIONS[confirming] : undefined;
  const first: Decision | null = found.canApprove ? "approve" : found.canCheck ? "check" : null;

  return (
    <form aria-label="Decision" className="flex flex-col gap-4 border-t border-divider pt-4" onSubmit={(event: FormEvent) => event.preventDefault()}>
      {found.status === "checked" && !found.canApprove ? (
        <Notice>You checked this application, so a different person must approve it. You can still send it back or reject it.</Notice>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-note`}>Note</Label>
        <Input
          id={`${ids}-note`}
          value={note}
          maxLength={MAX_NOTE}
          onChange={(event) => setNote(event.target.value)}
          aria-invalid={problem ? true : undefined}
          aria-describedby={`${ids}-note-help`}
          className={FIELD}
        />
        <FieldNote id={`${ids}-note-help`} error={problem}>
          Kept in the audit trail. If you send it back, the applicant sees this note.
        </FieldNote>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {asked && confirming ? (
        <div role="alertdialog" aria-label="Confirm" className="flex flex-col gap-3 rounded-md bg-warning-surface p-3.5">
          <p className="m-0 text-md font-semibold text-warning-text">{asked.text}</p>
          <div className="flex flex-wrap gap-2.5">
            <Button ref={confirmButton} disabled={busy} onClick={() => attempt(confirming, true)}>
              {busy ? "Saving" : asked.confirm}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setConfirming(null)}>
              Go back
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2.5">
          {first ? (
            <Button disabled={busy} onClick={() => attempt(first)} className="h-12 flex-[1_1_160px]">
              {busy ? "Saving" : first === "approve" ? "Approve and open the account" : "Mark as checked"}
            </Button>
          ) : null}
          {found.canSendBack ? (
            <Button variant="secondary" disabled={busy} onClick={() => attempt("send_back")} className="h-12 flex-[1_1_140px]">
              Send back
            </Button>
          ) : null}
          {found.canReject ? (
            <Button variant="secondary" disabled={busy} onClick={() => attempt("reject")} className="h-12 flex-[1_1_120px]">
              Reject
            </Button>
          ) : null}
        </div>
      )}
    </form>
  );
}
