import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/auth/context.ts";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { emptyDirectory, type Directory, type StaffCard } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import {
  complaintsErrorMessage,
  notConnectedComplaintsApi,
  type ComplaintDetail,
  type ComplaintsApi,
  type NewComplaint,
  type Register,
  type Show,
} from "./api.ts";
import { DetailPanel, type Command } from "./Detail.tsx";
import { LogForm } from "./LogForm.tsx";
import { Figures, RegisterTable } from "./Register.tsx";
import { createHttpComplaintsApi } from "./http.ts";
import { createStubComplaintsApi } from "./stub.ts";

const SUMMARY = "The formal register. Every complaint has an owner, a deadline and an outcome.";
const SHOWS: { id: Show; label: string }[] = [
  { id: "open", label: "Open" },
  { id: "closed", label: "Closed" },
  { id: "all", label: "All" },
];
const NOBODY = "No one yet";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

interface ComplaintsScreenProps {
  api: ComplaintsApi;
  directory: Directory;
  /** The signed-in member of staff. */
  me: string;
  /** `complaints.manage`: log, assign, move, add notes, and propose an outcome. */
  canManage: boolean;
  /** `complaints.close`: confirm or reject someone else's outcome. The API checks both again. */
  canClose: boolean;
  /** A key for each new complaint, kept until the API answers, so a retry logs it once. */
  newKey?: () => string;
  /** The Kampala business day, as a calendar date. */
  today?: () => string;
}

const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

export function ComplaintsScreen({
  api,
  directory,
  me,
  canManage,
  canClose,
  newKey = () => crypto.randomUUID(),
  today = kampalaToday,
}: ComplaintsScreenProps) {
  const [show, setShow] = useState<Show>("open");
  const [register, setRegister] = useState<Load<Register>>({ state: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Load<ComplaintDetail> | null>(null);
  const [logging, setLogging] = useState(false);
  // The key for the complaint in the log form. A new one only once the last was logged.
  const logKey = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [clients, setClients] = useState<Record<string, string | null>>({});
  const [staff, setStaff] = useState<Record<string, string | null>>({});
  const [team, setTeam] = useState<StaffCard[]>([]);
  // Bumped after each command, so the panel's half-filled forms are dropped once the complaint moves on.
  const [round, setRound] = useState(0);
  const asked = useRef(new Set<string>());
  // A complaint to open once the list for a new tab arrives, such as one just logged.
  const openNext = useRef<string | null>(null);

  const lookUp = useCallback(
    (clientIds: readonly string[], staffIds: readonly (string | null)[]) => {
      for (const clientId of clientIds) {
        if (asked.current.has(`client:${clientId}`)) continue;
        asked.current.add(`client:${clientId}`);
        void directory.client(clientId).then(
          (card) => setClients((known) => ({ ...known, [clientId]: card?.name ?? null })),
          () => setClients((known) => ({ ...known, [clientId]: null })),
        );
      }
      for (const staffId of staffIds) {
        if (!staffId || asked.current.has(`staff:${staffId}`)) continue;
        asked.current.add(`staff:${staffId}`);
        void directory.staffName(staffId).then(
          (name) => setStaff((known) => ({ ...known, [staffId]: name })),
          () => setStaff((known) => ({ ...known, [staffId]: null })),
        );
      }
    },
    [directory],
  );

  const fetchRegister = useCallback(
    (which: Show): Promise<Load<Register>> =>
      api.register(which).then(
        (value) => {
          lookUp(
            value.complaints.map((complaint) => complaint.clientId),
            value.complaints.map((complaint) => complaint.ownerId),
          );
          return { state: "ready", value };
        },
        (error: unknown) => ({ state: "failed", message: complaintsErrorMessage(error) }),
      ),
    [api, lookUp],
  );

  const openDetail = useCallback(
    (complaintId: string) => {
      setDetail({ state: "loading" });
      return api.complaint(complaintId).then(
        (value) => {
          lookUp([value.complaint.clientId], [value.complaint.ownerId, ...value.history.map((item) => item.actorId)]);
          setDetail({ state: "ready", value });
        },
        (error: unknown) => setDetail({ state: "failed", message: complaintsErrorMessage(error) }),
      );
    },
    [api, lookUp],
  );

  useEffect(() => {
    let current = true;
    void fetchRegister(show).then((next) => {
      if (!current) return;
      setRegister(next);
      // The first complaint opens by itself, as on the canvas.
      const wanted = openNext.current;
      openNext.current = null;
      if (wanted) {
        setSelected(wanted);
        void openDetail(wanted);
        return;
      }
      const first = next.state === "ready" ? next.value.complaints[0]?.id : undefined;
      setSelected(first ?? null);
      setDetail(null);
      if (first) void openDetail(first);
    });
    return () => {
      current = false;
    };
  }, [show, fetchRegister, openDetail]);

  useEffect(() => {
    let current = true;
    void directory.team().then(
      (people) => current && setTeam(people),
      () => current && setTeam([]),
    );
    return () => {
      current = false;
    };
  }, [directory]);

  const clientName = (clientId: string) => clients[clientId] ?? "Client";
  const staffName = (staffId: string | null) => (staffId ? (staff[staffId] ?? "Staff") : NOBODY);

  function select(complaintId: string) {
    setSelected(complaintId);
    setLogging(false);
    setFailed(null);
    void openDetail(complaintId);
  }

  async function afterChange(value: ComplaintDetail) {
    lookUp([value.complaint.clientId], [value.complaint.ownerId, ...value.history.map((item) => item.actorId)]);
    setDetail({ state: "ready", value });
    setSelected(value.complaint.id);
    setRound((count) => count + 1);
    const next = await fetchRegister(show);
    if (next.state === "ready") setRegister(next);
  }

  async function command(complaintId: string, next: Command): Promise<boolean> {
    setBusy(true);
    setFailed(null);
    try {
      const value = await (next.action === "assign"
        ? api.assign(complaintId, next.ownerId)
        : next.action === "move"
          ? api.move(complaintId, next.status, next.note)
          : next.action === "note"
            ? api.addNote(complaintId, next.note)
            : next.action === "propose"
              ? api.proposeOutcome(complaintId, next.closing)
              : api.decideOutcome(complaintId, next.proposalId, next.decision, next.reason));
      await afterChange(value);
      return true;
    } catch (error) {
      setFailed({ on: complaintId, message: complaintsErrorMessage(error) });
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function log(complaint: Omit<NewComplaint, "clientKey">) {
    setBusy(true);
    setFailed(null);
    logKey.current ??= newKey();
    try {
      const value = await api.log({ ...complaint, clientKey: logKey.current });
      logKey.current = null;
      setLogging(false);
      if (show === "closed") {
        // A new complaint is open, so show the open list with it selected.
        openNext.current = value.complaint.id;
        setRegister({ state: "loading" });
        setShow("open");
        return;
      }
      await afterChange(value);
    } catch (error) {
      setFailed({ on: "new", message: complaintsErrorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader title="Complaints" summary={SUMMARY} />
        {canManage ? (
          <Button
            className="px-[18px]"
            disabled={logging}
            onClick={() => {
              setLogging(true);
              setFailed(null);
            }}
          >
            Log a complaint
          </Button>
        ) : null}
      </div>
      {register.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading the register
        </p>
      ) : register.state === "failed" ? (
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">The register could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{register.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setRegister({ state: "loading" });
              void fetchRegister(show).then(setRegister);
            }}
          >
            Try again
          </Button>
        </section>
      ) : (
        <>
          <Figures summary={register.value.summary} />
          <div className="flex flex-wrap items-start gap-5">
            <div className="flex min-w-0 flex-[3_1_560px] flex-col gap-3">
              <div role="tablist" aria-label="Show" className="flex flex-wrap gap-2">
                {SHOWS.map(({ id, label }) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={show === id}
                    onClick={() => {
                      if (id === show) return;
                      setRegister({ state: "loading" });
                      setShow(id);
                    }}
                    className={cn(
                      "h-11 cursor-pointer rounded-pill px-4 text-sm",
                      show === id ? "bg-black font-bold text-white" : "border border-border-strong bg-surface font-semibold text-ink",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {register.value.complaints.length === 0 ? (
                <section className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
                  <h2 className="m-0 text-[17px] font-extrabold">
                    {show === "open" ? "No open complaints" : show === "closed" ? "No closed complaints" : "No complaints yet"}
                  </h2>
                  <p className="m-0 text-md leading-normal text-muted">
                    A help chat becomes a complaint when the client says they are unhappy or asks for one. Log it here, or the client
                    can make one in the app.
                  </p>
                </section>
              ) : (
                <RegisterTable
                  complaints={register.value.complaints}
                  selected={logging ? null : selected}
                  today={today()}
                  clientName={clientName}
                  // First names in the table, as on the canvas.
                  ownerName={(ownerId) => (ownerId ? (staffName(ownerId).split(" ")[0] ?? NOBODY) : NOBODY)}
                  onSelect={select}
                />
              )}
            </div>
            <aside aria-label="Complaint" className="flex min-w-0 flex-[1_1_300px] flex-col gap-4">
              {logging ? (
                <LogForm
                  directory={directory}
                  busy={busy}
                  error={failed?.on === "new" ? failed.message : null}
                  onSubmit={(complaint) => void log(complaint)}
                  onCancel={() => {
                    setLogging(false);
                    setFailed(null);
                  }}
                />
              ) : detail === null ? null : detail.state === "loading" ? (
                <p role="status" className="m-0 text-md text-muted">
                  Opening the complaint
                </p>
              ) : detail.state === "failed" ? (
                <section role="alert" className="flex flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
                  <h2 className="m-0 text-xl font-extrabold">This complaint could not be opened</h2>
                  <p className="m-0 text-md leading-normal text-muted">{detail.message}</p>
                  {selected ? (
                    <Button variant="secondary" onClick={() => void openDetail(selected)}>
                      Try again
                    </Button>
                  ) : null}
                </section>
              ) : (
                <DetailPanel
                  key={`${detail.value.complaint.id}:${round}`}
                  detail={detail.value}
                  clientName={clientName(detail.value.complaint.clientId)}
                  staffName={staffName}
                  team={team}
                  me={me}
                  canManage={canManage}
                  canClose={canClose}
                  busy={busy}
                  error={failed?.on === detail.value.complaint.id ? failed.message : null}
                  onCommand={(next) => command(detail.value.complaint.id, next)}
                />
              )}
            </aside>
          </div>
        </>
      )}
    </>
  );
}

export default function ComplaintsPage() {
  const { session } = useAuth();
  // A sample register under `pnpm dev` or a preview build, like sign-in. Otherwise the real API.
  const [{ api, directory }] = useState<{ api: ComplaintsApi; directory: Directory }>(() => {
    if (isStubAuth() && session) {
      const me = { id: session.staffId, name: session.name };
      // Under the stub, `?sample=empty` shows the screen with nothing in it and `?sample=offline` as if Finvest could not be reached.
      const sample = new URLSearchParams(window.location.search).get("sample");
      if (sample === "offline") return { api: notConnectedComplaintsApi, directory: createStubDirectory(me) };
      return { api: createStubComplaintsApi(me, kampalaToday, Date.now, sample === "empty" ? "empty" : "sample"), directory: createStubDirectory(me) };
    }
    const base = apiBaseUrl();
    if (!base) return { api: notConnectedComplaintsApi, directory: emptyDirectory };
    const http = createHttp(base);
    return { api: createHttpComplaintsApi(http), directory: createHttpDirectory(http) };
  });
  return (
    <ComplaintsScreen
      api={api}
      directory={directory}
      me={session?.staffId ?? ""}
      canManage={session ? can(session, "complaints.manage") : false}
      canClose={session ? can(session, "complaints.close") : false}
    />
  );
}
