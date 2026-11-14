import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import type { Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { stamp } from "./labels.ts";
import {
  BOUNDS,
  CHANNEL_LABELS,
  CHANNELS,
  formatDay,
  inNetwork,
  problemText,
  remindersText,
  SettingsError,
  settingsErrorMessage,
  shillings,
  type Channels,
  type Decision,
  type Holiday,
  type Limits,
  type LimitsApi,
  type LimitsScreen,
  type OfficeNetwork,
  type PlatformSettings,
  type PlatformSettingsApi,
  type Problem,
  type Proposal,
  type SettingKey,
  type StatementLayout,
  type Values,
} from "./platform.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const SMALL_INPUT = "min-w-0 rounded-lg border border-border-strong bg-surface px-3 py-2.5 text-md font-medium text-ink aria-invalid:border-danger";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; data: T };
type Failure = { on: string; message: string; problems: readonly Problem[] };

interface PlatformSectionsProps {
  settings: PlatformSettingsApi;
  limits: LimitsApi;
  directory: Directory;
}

/**
 * The Settings cards drawn on the canvas: limits and automatic approval, payment channels, calendar,
 * staff security and statement import. Every value is changed by two people.
 */
export function PlatformSections({ settings, limits, directory }: PlatformSectionsProps) {
  const [platform, setPlatform] = useState<Load<PlatformSettings>>({ state: "loading" });
  const [screen, setScreen] = useState<Load<LimitsScreen>>({ state: "loading" });
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<Failure | null>(null);
  const [names, setNames] = useState<Record<string, string | null>>({});
  const asked = useRef(new Set<string>());

  const lookUp = useCallback(
    (staffIds: readonly string[]) => {
      for (const staffId of staffIds) {
        if (asked.current.has(staffId)) continue;
        asked.current.add(staffId);
        void directory.staffName(staffId).then(
          (name) => setNames((known) => ({ ...known, [staffId]: name })),
          () => setNames((known) => ({ ...known, [staffId]: null })),
        );
      }
    },
    [directory],
  );

  const fetchPlatform = useCallback(
    (): Promise<Load<PlatformSettings>> =>
      settings.list().then(
        (data) => {
          lookUp(Object.values(data.settings).flatMap((setting) => (setting.proposal ? [setting.proposal.proposedBy] : [])));
          return { state: "ready", data };
        },
        (error: unknown) => ({ state: "failed", message: settingsErrorMessage(error) }),
      ),
    [settings, lookUp],
  );
  const fetchLimits = useCallback(
    (): Promise<Load<LimitsScreen>> =>
      limits.get().then(
        (data) => {
          lookUp(data.change ? [data.change.proposedBy] : []);
          return { state: "ready", data };
        },
        (error: unknown) => ({ state: "failed", message: settingsErrorMessage(error) }),
      ),
    [limits, lookUp],
  );

  useEffect(() => {
    let current = true;
    void fetchPlatform().then((next) => current && setPlatform(next));
    void fetchLimits().then((next) => current && setScreen(next));
    return () => {
      current = false;
    };
  }, [fetchPlatform, fetchLimits]);

  async function run(on: string, command: () => Promise<void>): Promise<boolean> {
    setBusy(true);
    setFailed(null);
    try {
      await command();
      setEditing(null);
      return true;
    } catch (error) {
      setFailed({ on, message: settingsErrorMessage(error), problems: error instanceof SettingsError ? error.problems : [] });
      return false;
    } finally {
      // The cards always show what Finvest now holds, after a change or a refusal.
      const [fresh, freshLimits] = await Promise.all([fetchPlatform(), fetchLimits()]);
      if (fresh.state === "ready") setPlatform(fresh);
      if (freshLimits.state === "ready") setScreen(freshLimits);
      setBusy(false);
    }
  }

  const shared = {
    busy,
    staffName: (staffId: string) => names[staffId] ?? "a member of staff",
    editing,
    failed,
    onEdit: (on: string | null) => {
      setEditing(on);
      setFailed(null);
    },
  };

  function item<K extends SettingKey>(key: K, data: PlatformSettings, view: ItemView<Values[K]>) {
    const setting = data.settings[key];
    return (
      <SettingItem<Values[K]>
        key={`${key}:${setting.proposal?.id ?? "none"}:${setting.changedAt ?? "default"}`}
        id={key}
        value={setting.value}
        proposal={setting.proposal}
        changedAt={setting.changedAt}
        canChange={setting.canChange}
        view={view}
        onPropose={(value, reason) => run(key, () => settings.propose(key, value, reason))}
        onDecide={(decision, reason) =>
          setting.proposal ? run(key, () => settings.decide(setting.proposal?.id ?? "", decision, reason)) : Promise.resolve(false)
        }
        {...shared}
      />
    );
  }

  const platformCard = (title: string, body: (data: PlatformSettings) => ReactNode, note?: ReactNode) => (
    <Card title={title} note={note}>
      {platform.state === "loading" ? (
        <p role="status" className="m-0 py-3 text-md text-muted">
          Loading
        </p>
      ) : platform.state === "failed" ? (
        <Failed
          message={platform.message}
          onRetry={() => {
            setPlatform({ state: "loading" });
            void fetchPlatform().then(setPlatform);
          }}
        />
      ) : (
        body(platform.data)
      )}
    </Card>
  );

  return (
    <section aria-labelledby="platform-rules" className="flex flex-col gap-4">
      <div className="flex max-w-[720px] flex-col gap-1">
        <h2 id="platform-rules" className="m-0 text-xl font-extrabold">
          Platform rules
        </h2>
        <p className="m-0 text-md leading-normal text-muted">
          Limits, channels, the calendar and staff security. One person proposes a change and someone else approves it. Until then
          the value in use stays as it is.
        </p>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,440px),1fr))] items-start gap-5">
        <Card title="Limits and automatic approval">
          {screen.state === "loading" ? (
            <p role="status" className="m-0 py-3 text-md text-muted">
              Loading
            </p>
          ) : screen.state === "failed" ? (
            <Failed
              message={screen.message}
              onRetry={() => {
                setScreen({ state: "loading" });
                void fetchLimits().then(setScreen);
              }}
            />
          ) : (
            <>
              <SettingItem<Limits>
                key={`limits:${screen.data.change?.id ?? "none"}:${screen.data.inForce.effectiveFrom}:${screen.data.upcoming.length}`}
                id="limits"
                value={screen.data.inForce}
                proposal={screen.data.change}
                changedAt={null}
                canChange={screen.data.canChange}
                view={LIMITS_VIEW}
                onPropose={(value, reason) => run("limits", () => limits.propose(value, reason))}
                onDecide={(decision, reason) =>
                  screen.data.change ? run("limits", () => limits.decide(screen.data.change?.id ?? "", decision, reason)) : Promise.resolve(false)
                }
                {...shared}
              />
              {screen.data.upcoming.map((version) => (
                <div key={version.effectiveFrom} className="flex flex-col gap-2 border-t border-divider py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-md font-bold">From {formatDay(version.effectiveFrom)}</span>
                    <Chip tone="neutral">Approved, starts later</Chip>
                  </div>
                  <LimitsTable limits={version} compare={screen.data.inForce} />
                </div>
              ))}
              <Row label="Automatic identity approval">
                <span className="text-muted">Off. A person reviews every client</span>
              </Row>
            </>
          )}
        </Card>

        {platformCard("Payment channels", (data) => item("payment_channels", data, CHANNELS_VIEW))}

        {platformCard("Calendar and timing", (data) => (
          <>
            {item("bank_payout_cutoff", data, CUTOFF_VIEW)}
            {item("maturity_reminders", data, REMINDERS_VIEW)}
            {item("public_holidays", data, HOLIDAYS_VIEW)}
          </>
        ))}

        {platformCard(
          "Staff security",
          (data) => (
            <>
              <Row label="Password and passkey or app code for all staff">
                <span>Required, no SMS</span>
              </Row>
              {item("staff_idle_minutes", data, IDLE_VIEW)}
              {item("office_networks", data, networksView(data.yourAddress))}
              <p className="m-0 border-t border-divider pt-3 text-xs leading-normal text-muted">
                {data.yourAddress ? `You are on ${data.yourAddress}. ` : ""}A list of networks that leaves out where you are, or where
                the approver is, is refused so nobody locks themselves out.
              </p>
            </>
          ),
        )}

        {platformCard("Statement import", (data) => item("statement_layouts", data, layoutsView(data.dateFormats)))}
      </div>
    </section>
  );
}

// Layout pieces -------------------------------------------------------------------------------

function Card({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <article aria-label={title} className="flex min-w-0 flex-col rounded-[20px] border border-border bg-surface px-5 pt-[18px] pb-3">
      <h3 className="m-0 mb-1.5 text-[16px] font-extrabold">{title}</h3>
      {note}
      {children}
    </article>
  );
}

function Row({ label, children, chip }: { label: string; children: ReactNode; chip?: ReactNode }) {
  return (
    <div className="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-divider py-2 text-md">
      <span className="min-w-0">{label}</span>
      <span className="flex flex-wrap items-center justify-end gap-2 text-right font-bold">
        {chip}
        {children}
      </span>
    </div>
  );
}

function Chip({ tone, children }: { tone: "warning" | "success" | "neutral" | "danger"; children: ReactNode }) {
  const tones = {
    warning: "bg-warning-surface text-warning-text",
    success: "bg-success-surface text-success",
    neutral: "bg-surface-sunken text-muted",
    danger: "bg-danger-surface text-danger",
  };
  return <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold whitespace-nowrap", tones[tone])}>{children}</span>;
}

function Refusal({ message }: { message: string }) {
  return (
    <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
      {message}
    </p>
  );
}

function Failed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-3 border-t border-divider py-3">
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      <Button variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}

// One setting ---------------------------------------------------------------------------------

interface ItemView<V> {
  label: string;
  /** The value in a few words, for the right of the row. */
  summary: (value: V) => ReactNode;
  /** The full value, under the row. Compared with the value in use when it is a proposal. */
  details?: (value: V, inUse?: V) => ReactNode;
  /** What changes for clients or staff once it is approved. */
  effect: string;
  editor: (props: { draft: V; setDraft: (value: V) => void; tried: boolean }) => ReactNode;
  /** What the backend would refuse, checked as the person types. The backend decides. */
  check: (value: V) => string | null;
  /** The value a new proposal starts from. */
  start?: (value: V) => V;
}

interface SettingItemProps<V> {
  id: string;
  value: V;
  proposal: Proposal<V> | null;
  changedAt: string | null;
  canChange: boolean;
  view: ItemView<V>;
  busy: boolean;
  staffName: (staffId: string) => string;
  editing: string | null;
  failed: Failure | null;
  onEdit: (on: string | null) => void;
  onPropose: (value: V, reason: string) => Promise<boolean>;
  onDecide: (decision: Decision, reason: string) => Promise<boolean>;
}

function SettingItem<V>({ id, value, proposal, changedAt, canChange, view, busy, staffName, editing, failed, onEdit, onPropose, onDecide }: SettingItemProps<V>) {
  const failure = failed?.on === id ? failed : null;
  const open = editing === id;
  return (
    <div className="flex flex-col">
      <Row
        label={view.label}
        chip={proposal ? <Chip tone="warning">Change waiting</Chip> : null}
      >
        {view.summary(value)}
        {canChange && !proposal && !open ? (
          <button type="button" className="cursor-pointer rounded-pill px-2 py-1 text-sm font-bold underline underline-offset-2" onClick={() => onEdit(id)} aria-label={`Change ${view.label.toLowerCase()}`}>
            Change
          </button>
        ) : null}
      </Row>
      {view.details ? <div className="pb-3">{view.details(value)}</div> : null}
      {changedAt && !open ? <p className="m-0 -mt-1 pb-2 text-xs text-muted">Approved {stamp(changedAt)}</p> : null}
      {proposal ? (
        <ProposalBlock key={proposal.id} value={value} proposal={proposal} view={view} canChange={canChange} staffName={staffName} busy={busy} failure={failure} onDecide={onDecide} />
      ) : open ? (
        <ProposeForm value={value} view={view} busy={busy} failure={failure} onCancel={() => onEdit(null)} onSubmit={onPropose} />
      ) : failure ? (
        <div className="pb-3">
          <Refusal message={failure.message} />
        </div>
      ) : null}
    </div>
  );
}

function ProposalBlock<V>({
  value,
  proposal,
  view,
  canChange,
  staffName,
  busy,
  failure,
  onDecide,
}: {
  value: V;
  proposal: Proposal<V>;
  view: ItemView<V>;
  canChange: boolean;
  staffName: (staffId: string) => string;
  busy: boolean;
  failure: Failure | null;
  onDecide: (decision: Decision, reason: string) => Promise<boolean>;
}) {
  const [deciding, setDeciding] = useState<Decision | null>(null);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const missing = tried && note.trim().length === 0;
  return (
    <section aria-label={`${view.label}: change waiting for a second person`} className="mb-3 flex flex-col gap-3 rounded-[16px] border border-warning-surface bg-paper p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="m-0 text-md font-extrabold">Proposed</h4>
        <span className="text-md font-bold">{view.summary(proposal.value)}</span>
      </div>
      {view.details ? view.details(proposal.value, value) : null}
      <p className="m-0 text-sm leading-normal text-muted">
        Proposed by {proposal.yours ? "you" : staffName(proposal.proposedBy)}, {stamp(proposal.proposedAt)}. {proposal.reason}
      </p>
      {failure ? <Refusal message={failure.message} /> : null}
      {!canChange ? <p className="m-0 text-sm text-muted">Someone whose role can change this approves it.</p> : null}
      {canChange && !deciding ? (
        proposal.yours ? (
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-muted">Someone else must approve or reject your change.</span>
            <Button variant="secondary" disabled={busy} onClick={() => setDeciding("withdrawn")}>
              Withdraw it
            </Button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="m-0 text-sm leading-normal text-muted">{view.effect}</p>
            <div className="flex gap-2">
              <Button disabled={busy} onClick={() => setDeciding("approved")}>
                Approve
              </Button>
              <Button variant="secondary" disabled={busy} onClick={() => setDeciding("rejected")}>
                Reject
              </Button>
            </div>
          </div>
        )
      ) : null}
      {deciding ? (
        <form
          aria-label="Decide the change"
          className="flex flex-col gap-2 border-t border-divider pt-3"
          onSubmit={(event) => {
            event.preventDefault();
            setTried(true);
            if (note.trim().length === 0) return;
            void onDecide(deciding, note);
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            {deciding === "approved" ? "Why approve it?" : deciding === "rejected" ? "What is wrong with it?" : "Why withdraw it?"}
            <textarea value={note} rows={2} maxLength={BOUNDS.reason} aria-invalid={missing} onChange={(event) => setNote(event.target.value)} className={INPUT} />
          </label>
          {missing ? <p className="m-0 text-xs font-bold text-danger">Write a short reason for the audit trail.</p> : null}
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Saving" : deciding === "approved" ? "Approve the change" : deciding === "rejected" ? "Reject the change" : "Withdraw the change"}
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setDeciding(null);
                setTried(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </section>
  );
}

function ProposeForm<V>({
  value,
  view,
  busy,
  failure,
  onCancel,
  onSubmit,
}: {
  value: V;
  view: ItemView<V>;
  busy: boolean;
  failure: Failure | null;
  onCancel: () => void;
  onSubmit: (value: V, reason: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<V>(() => (view.start ? view.start(value) : value));
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const problem = view.check(draft);
  const noReason = reason.trim().length === 0;
  const refused = failure?.problems.filter((item) => item.field !== "reason") ?? [];
  return (
    <form
      aria-label={`New ${view.label.toLowerCase()}`}
      className="mb-3 flex flex-col gap-3 rounded-[16px] border border-border bg-paper p-4"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (problem || noReason) return;
        void onSubmit(draft, reason);
      }}
    >
      {view.editor({ draft, setDraft, tried })}
      {problem && tried ? <p className="m-0 text-xs font-bold text-danger">{problem}</p> : null}
      {refused.map((item) => (
        <p key={`${item.field}:${item.code}`} className="m-0 text-xs font-bold text-danger">
          {problemText(item)}
        </p>
      ))}
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        Why is it changing?
        <span className="text-xs font-medium text-muted">Staff only, for the person who approves it and the audit trail.</span>
        <textarea value={reason} rows={2} maxLength={BOUNDS.reason} aria-invalid={tried && noReason} onChange={(event) => setReason(event.target.value)} className={INPUT} />
      </label>
      {tried && noReason ? <p className="m-0 text-xs font-bold text-danger">Say why, for the second person and the audit trail.</p> : null}
      {failure ? <Refusal message={failure.message} /> : null}
      <p className="m-0 text-xs leading-normal text-muted">The value in use stays as it is until someone else approves this.</p>
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving" : "Send for approval"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string | undefined; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5 text-sm font-bold">
      {label}
      {hint ? <span className="text-xs font-medium text-muted">{hint}</span> : null}
      {children}
    </label>
  );
}

function Changed({ on }: { on: boolean }) {
  return on ? <span className="ml-2 rounded-pill bg-warning-surface px-2 py-0.5 text-xs font-bold text-warning-text">Changed</span> : null;
}

// Withdrawal limits ---------------------------------------------------------------------------

const LIMIT_ROWS: readonly { key: Exclude<keyof Limits, "effectiveFrom">; label: string }[] = [
  { key: "mobileMoneyPerPayout", label: "Mobile money, one payout" },
  { key: "mobileMoneyPerDay", label: "Mobile money, per day" },
  { key: "bankPerPayout", label: "Bank, one payout" },
  { key: "bankPerDay", label: "Bank, per day" },
  { key: "reviewAbove", label: "Second approval for payouts and transfers above" },
];

function LimitsTable({ limits, compare }: { limits: Limits; compare?: Limits | undefined }) {
  return (
    <dl className="m-0 grid grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-x-4 gap-y-1.5 text-md">
      {LIMIT_ROWS.map(({ key, label }) => (
        <div key={key} className="contents">
          <dt className="text-sm text-muted">{label}</dt>
          <dd className="m-0 flex flex-wrap items-center justify-end gap-y-1 text-right font-bold tabular-nums">
            <span className="whitespace-nowrap">{shillings(limits[key])}</span>
            <Changed on={compare !== undefined && compare[key] !== limits[key]} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Tomorrow in Kampala, the earliest day new limits may start. */
function tomorrow(): string {
  const kampala = new Date(Date.now() + 3 * 60 * 60 * 1000);
  kampala.setUTCDate(kampala.getUTCDate() + 1);
  return kampala.toISOString().slice(0, 10);
}

const amountText = (amount: number | null) => (amount === null ? "" : String(amount));
const amountValue = (text: string): number | null => {
  const digits = text.replace(/[\s,]/g, "");
  return digits.length === 0 ? null : Number(digits);
};

const LIMITS_VIEW: ItemView<Limits> = {
  label: "Limits in force",
  summary: (value) => `From ${formatDay(value.effectiveFrom)}`,
  details: (value, inUse) => (
    <LimitsTable limits={value} compare={inUse} />
  ),
  effect: "Once approved, these limits apply to every payout asked for from the start day. Payouts already held or queued keep the limits they were checked against.",
  start: (value) => ({ ...value, effectiveFrom: tomorrow() }),
  check: (value) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value.effectiveFrom) || value.effectiveFrom < tomorrow()) return "Pick a start day after today. Limits never change part way through a day.";
    const amounts = LIMIT_ROWS.map(({ key }) => value[key]);
    if (amounts.some((amount) => amount !== null && (!Number.isInteger(amount) || amount < 1 || amount > BOUNDS.amount))) return "Write each limit in whole shillings, or leave it empty for no limit.";
    if (value.mobileMoneyPerPayout === null || Number.isNaN(value.mobileMoneyPerPayout)) return "The mobile money limit for one payout is always set.";
    if (value.mobileMoneyPerPayout > BOUNDS.mobileMoneyPerPayout) return "A mobile money wallet takes at most UGX 5,000,000 in one transfer.";
    if (value.mobileMoneyPerDay !== null && value.mobileMoneyPerDay < value.mobileMoneyPerPayout) return "The daily mobile money limit cannot be below the limit for one payout.";
    if (value.bankPerDay !== null && value.bankPerPayout !== null && value.bankPerDay < value.bankPerPayout) return "The daily bank limit cannot be below the limit for one payout.";
    return null;
  },
  editor: ({ draft, setDraft }) => (
    <>
      <Field label="Starts on" hint="A later day than today, Kampala time.">
        <input type="date" min={tomorrow()} value={draft.effectiveFrom} onChange={(event) => setDraft({ ...draft, effectiveFrom: event.target.value })} className={INPUT} />
      </Field>
      {LIMIT_ROWS.map(({ key, label }) => (
        <Field key={key} label={`${label} (UGX)`} hint={key === "mobileMoneyPerPayout" ? "At most 5,000,000." : "Leave empty for no limit."}>
          <input
            inputMode="numeric"
            value={amountText(draft[key])}
            onChange={(event) => setDraft({ ...draft, [key]: amountValue(event.target.value) })}
            className={cn(INPUT, "tabular-nums")}
          />
        </Field>
      ))}
    </>
  ),
};

// Payment channels ----------------------------------------------------------------------------

const openCount = (value: Channels) => CHANNELS.filter((channel) => value[channel]).length;

const CHANNELS_VIEW: ItemView<Channels> = {
  label: "Channels open",
  summary: (value) => (openCount(value) === CHANNELS.length ? "All open" : `${openCount(value)} of ${CHANNELS.length} open`),
  details: (value, inUse) => (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-md">
      {CHANNELS.map((channel) => (
        <li key={channel} className="flex items-center justify-between gap-3">
          <span>{CHANNEL_LABELS[channel]}</span>
          <span className="flex items-center">
            {value[channel] ? <Chip tone="success">Open</Chip> : <Chip tone="danger">Paused</Chip>}
            <Changed on={inUse !== undefined && inUse[channel] !== value[channel]} />
          </span>
        </li>
      ))}
    </ul>
  ),
  effect: "A paused channel refuses new payments or withdrawals at once. Money already on its way finishes as it would have, and payouts at maturity still go out.",
  check: () => null,
  editor: ({ draft, setDraft }) => (
    <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
      <legend className="mb-1 text-sm font-bold">Open channels</legend>
      {CHANNELS.map((channel) => (
        <label key={channel} className="flex items-center gap-3 text-md">
          <input type="checkbox" className="size-5" checked={draft[channel]} onChange={(event) => setDraft({ ...draft, [channel]: event.target.checked })} />
          {CHANNEL_LABELS[channel]}
        </label>
      ))}
    </fieldset>
  ),
};

// Calendar ------------------------------------------------------------------------------------

const CUTOFF_VIEW: ItemView<string> = {
  label: "Same day bank payout cut-off",
  summary: (value) => value,
  effect: "A bank payout asked for after this time, Kampala time, is paid on the next working day.",
  check: (value) => (/^\d{2}:\d{2}$/.test(value) && value >= "08:00" && value <= "17:00" ? null : "Pick a time between 08:00 and 17:00."),
  editor: ({ draft, setDraft }) => (
    <Field label="Cut-off, Kampala time" hint="Between 08:00 and 17:00.">
      <input type="time" min="08:00" max="17:00" step={60} value={draft} onChange={(event) => setDraft(event.target.value)} className={INPUT} />
    </Field>
  ),
};

const REMINDERS_VIEW: ItemView<number[]> = {
  label: "Maturity reminders",
  summary: remindersText,
  effect: "Clients are reminded this many days before an investment matures, from the next morning's run.",
  start: (value) => value,
  check: (value) => {
    if (value.length > BOUNDS.reminders) return "At most three reminders.";
    if (value.some((day) => !Number.isInteger(day) || day < 1 || day > BOUNDS.daysBefore)) return "Each reminder is 1 to 30 days before.";
    if (new Set(value).size !== value.length) return "One day is listed twice.";
    return null;
  },
  editor: ({ draft, setDraft }) => <RemindersEditor draft={draft} setDraft={setDraft} />,
};

function RemindersEditor({ draft, setDraft }: { draft: number[]; setDraft: (value: number[]) => void }) {
  const [text, setText] = useState(draft.join(", "));
  return (
    <Field label="Days before maturity" hint="Up to three, separated by commas, such as 7, 1. Leave empty to send none.">
      <input
        value={text}
        inputMode="numeric"
        onChange={(event) => {
          setText(event.target.value);
          const parts = event.target.value.split(/[\s,]+/).filter(Boolean);
          setDraft(parts.map(Number).sort((a, b) => b - a));
        }}
        className={INPUT}
      />
    </Field>
  );
}

const HOLIDAYS_VIEW: ItemView<Holiday[]> = {
  label: "Public holidays",
  summary: (value) => {
    const years = [...new Set(value.map((day) => day.date.slice(0, 4)))].sort();
    return value.length === 0 ? "None" : `${value.length} days, ${years.join(" and ")}`;
  },
  details: (value, inUse) => <HolidayList days={value} inUse={inUse} />,
  effect: "Holidays are not working days. Bank payouts and reminders move to the next working day from the moment it is approved.",
  check: (value) => {
    if (value.length > BOUNDS.holidays) return "At most 60 days.";
    if (new Set(value.map((day) => day.date)).size !== value.length) return "Two holidays are on the same day.";
    return null;
  },
  editor: ({ draft, setDraft }) => <HolidaysEditor draft={draft} setDraft={setDraft} />,
};

function HolidayList({ days, inUse }: { days: readonly Holiday[]; inUse?: readonly Holiday[] | undefined }) {
  const added = inUse ? days.filter((day) => !inUse.some((old) => old.date === day.date && old.name === day.name)) : [];
  const removed = inUse ? inUse.filter((old) => !days.some((day) => day.date === old.date && day.name === old.name)) : [];
  if (inUse) {
    return (
      <ul className="m-0 flex list-none flex-col gap-1 p-0 text-md">
        {added.map((day) => (
          <li key={`+${day.date}`} className="flex justify-between gap-3">
            <span>
              <Chip tone="success">Added</Chip> {day.name}
            </span>
            <span className="font-semibold tabular-nums">{formatDay(day.date)}</span>
          </li>
        ))}
        {removed.map((day) => (
          <li key={`-${day.date}`} className="flex justify-between gap-3">
            <span>
              <Chip tone="danger">Removed</Chip> {day.name}
            </span>
            <span className="font-semibold tabular-nums">{formatDay(day.date)}</span>
          </li>
        ))}
        {added.length === 0 && removed.length === 0 ? <li className="text-muted">No days added or removed.</li> : null}
      </ul>
    );
  }
  return (
    <details className="text-md">
      <summary className="cursor-pointer text-sm font-bold">Show the days</summary>
      <ul className="m-0 mt-2 flex list-none flex-col gap-1 p-0">
        {days.map((day) => (
          <li key={day.date} className="flex justify-between gap-3">
            <span>{day.name}</span>
            <span className="font-semibold tabular-nums">{formatDay(day.date)}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function HolidaysEditor({ draft, setDraft }: { draft: Holiday[]; setDraft: (value: Holiday[]) => void }) {
  const [date, setDate] = useState("");
  const [name, setName] = useState("");
  const ready = /^\d{4}-\d{2}-\d{2}$/.test(date) && name.trim().length > 0;
  return (
    <div className="flex flex-col gap-3">
      <ul aria-label="Holidays" className="m-0 flex list-none flex-col gap-1 p-0 text-md">
        {draft.map((day) => (
          <li key={day.date} className="flex items-center justify-between gap-3">
            <span className="min-w-0">
              {day.name} <span className="text-sm text-muted tabular-nums">{formatDay(day.date)}</span>
            </span>
            <button type="button" className="cursor-pointer text-sm font-bold text-danger underline" onClick={() => setDraft(draft.filter((item) => item.date !== day.date))} aria-label={`Remove ${day.name}`}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Day">
          <input type="date" value={date} onChange={(event) => setDate(event.target.value)} className={SMALL_INPUT} />
        </Field>
        <div className="min-w-[160px] flex-1">
          <Field label="Name">
            <input value={name} maxLength={BOUNDS.holidayName} onChange={(event) => setName(event.target.value)} className={cn(SMALL_INPUT, "w-full")} />
          </Field>
        </div>
        <Button
          variant="secondary"
          disabled={!ready}
          onClick={() => {
            setDraft([...draft.filter((item) => item.date !== date), { date, name: name.trim() }].sort((a, b) => a.date.localeCompare(b.date)));
            setDate("");
            setName("");
          }}
        >
          Add the day
        </Button>
      </div>
    </div>
  );
}

// Staff security ------------------------------------------------------------------------------

const IDLE_VIEW: ItemView<number> = {
  label: "Sign out after no activity",
  summary: (value) => `${value} minutes`,
  effect: "From the next request, a member of staff idle for longer than this is signed out. It never stretches a session past its absolute limit.",
  check: (value) => (Number.isInteger(value) && value >= BOUNDS.idleMin && value <= BOUNDS.idleMax ? null : "Pick 5 to 240 minutes."),
  editor: ({ draft, setDraft }) => (
    <Field label="Minutes" hint="5 to 240.">
      <input type="number" min={BOUNDS.idleMin} max={BOUNDS.idleMax} value={Number.isNaN(draft) ? "" : draft} onChange={(event) => setDraft(Number(event.target.value))} className={INPUT} />
    </Field>
  ),
};

function networksView(yourAddress: string | null): ItemView<OfficeNetwork[]> {
  return {
    label: "Allowed office networks",
    summary: (value) => (value.length === 0 ? "Any network" : value.length === 1 ? "1 network" : `${value.length} networks`),
    details: (value, inUse) =>
      value.length === 0 ? (
        inUse && inUse.length > 0 ? <p className="m-0 text-md text-muted">Any network, as before a list was set.</p> : null
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1 p-0 text-md">
          {value.map((item) => (
            <li key={item.network} className="flex justify-between gap-3">
              <span>
                {item.label}
                {inUse && !inUse.some((old) => old.network === item.network) ? <Changed on /> : null}
              </span>
              <span className="font-semibold tabular-nums">{item.network}</span>
            </li>
          ))}
        </ul>
      ),
    effect: "Once approved, the console works only from these networks. Check that the list holds every office and your own network.",
    check: (value) => {
      if (value.length > BOUNDS.networks) return "At most 20 networks.";
      if (value.length > 0 && yourAddress && !value.some((item) => inNetwork(yourAddress, item.network))) {
        return `This leaves out the network you are on (${yourAddress}), so Finvest would refuse it. Add your network first.`;
      }
      return null;
    },
    editor: ({ draft, setDraft }) => <NetworksEditor draft={draft} setDraft={setDraft} yourAddress={yourAddress} />,
  };
}

function NetworksEditor({ draft, setDraft, yourAddress }: { draft: OfficeNetwork[]; setDraft: (value: OfficeNetwork[]) => void; yourAddress: string | null }) {
  const [network, setNetwork] = useState("");
  const [label, setLabel] = useState("");
  const ready = network.trim().length > 0 && label.trim().length > 0;
  return (
    <div className="flex flex-col gap-3">
      {draft.length === 0 ? <p className="m-0 text-md text-muted">No list: staff may use the console from any network.</p> : null}
      <ul aria-label="Networks" className="m-0 flex list-none flex-col gap-1 p-0 text-md">
        {draft.map((item) => (
          <li key={item.network} className="flex items-center justify-between gap-3">
            <span className="min-w-0">
              {item.label} <span className="text-sm text-muted tabular-nums">{item.network}</span>
            </span>
            <button type="button" className="cursor-pointer text-sm font-bold text-danger underline" onClick={() => setDraft(draft.filter((other) => other.network !== item.network))} aria-label={`Remove ${item.label}`}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-[160px] flex-1">
          <Field label="Address or range" hint="Such as 196.43.130.0/24.">
            <input value={network} maxLength={50} onChange={(event) => setNetwork(event.target.value)} className={cn(SMALL_INPUT, "w-full")} />
          </Field>
        </div>
        <div className="min-w-[140px] flex-1">
          <Field label="Name">
            <input value={label} maxLength={BOUNDS.networkLabel} onChange={(event) => setLabel(event.target.value)} className={cn(SMALL_INPUT, "w-full")} />
          </Field>
        </div>
        <Button
          variant="secondary"
          disabled={!ready}
          onClick={() => {
            setDraft([...draft.filter((item) => item.network !== network.trim()), { network: network.trim(), label: label.trim() }]);
            setNetwork("");
            setLabel("");
          }}
        >
          Add
        </Button>
      </div>
      {yourAddress && !draft.some((item) => inNetwork(yourAddress, item.network)) ? (
        <div>
          <Button variant="secondary" onClick={() => setDraft([...draft, { network: yourAddress, label: "Where I am now" }])}>
            Add the network you are on ({yourAddress})
          </Button>
        </div>
      ) : null}
    </div>
  );
}

// Statement import ----------------------------------------------------------------------------

const EMPTY_LAYOUT: StatementLayout = {
  name: "",
  label: "",
  dateColumn: "",
  dateFormat: "DD/MM/YYYY",
  narrative: "",
  reference: null,
  moneyIn: "",
  moneyOut: "",
  amount: null,
  direction: null,
  inWord: null,
  outWord: null,
};

function layoutProblem(layout: StatementLayout): string | null {
  if (!/^[a-z][a-z0-9_]{1,39}$/.test(layout.name)) return "The short name is 2 to 40 lower case letters, digits or underscores, starting with a letter.";
  if (layout.name === "standard") return "That name is the built-in layout. Pick another.";
  if (layout.label.trim().length === 0 || layout.dateColumn.trim().length === 0 || layout.narrative.trim().length === 0) return "Give the layout a name, a date column and a description column.";
  const split = !!layout.moneyIn?.trim() && !!layout.moneyOut?.trim();
  const signed = !!layout.amount?.trim() && !!layout.direction?.trim() && !!layout.inWord?.trim() && !!layout.outWord?.trim();
  if (split === signed) return "Give money in and money out columns, or one amount column with a direction column and its two words.";
  if (signed && layout.inWord?.trim().toLowerCase() === layout.outWord?.trim().toLowerCase()) return "The word for money out must differ from the word for money in.";
  return null;
}

function layoutsView(dateFormats: readonly string[]): ItemView<StatementLayout[]> {
  return {
    label: "Statement layouts",
    summary: (value) => (value.length === 0 ? "Built-in layout only" : `Built-in and ${value.length} more`),
    details: (value, inUse) =>
      value.length === 0 ? (
        <p className="m-0 text-md text-muted">Statements are imported in the built-in layout: date, description, reference, money in and money out.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0 text-md">
          {value.map((layout) => (
            <li key={layout.name} className="flex flex-col gap-0.5">
              <span className="font-bold">
                {layout.label} <span className="text-sm font-medium text-muted">{layout.name}</span>
                {inUse && !inUse.some((old) => JSON.stringify(old) === JSON.stringify(layout)) ? <Changed on /> : null}
              </span>
              <span className="text-sm text-muted">
                {layout.dateColumn} ({layout.dateFormat}), {layout.narrative}
                {layout.moneyIn ? `, ${layout.moneyIn} and ${layout.moneyOut}` : `, ${layout.amount} with ${layout.direction} (${layout.inWord} or ${layout.outWord})`}
              </span>
            </li>
          ))}
        </ul>
      ),
    effect: "Once approved, staff can pick these layouts when they import a provider's statement on Reconciliation.",
    check: (value) => {
      if (value.length > BOUNDS.layouts) return "At most 10 layouts.";
      if (new Set(value.map((layout) => layout.name)).size !== value.length) return "Two layouts have the same name.";
      return null;
    },
    editor: ({ draft, setDraft }) => <LayoutsEditor draft={draft} setDraft={setDraft} dateFormats={dateFormats} />,
  };
}

function LayoutsEditor({ draft, setDraft, dateFormats }: { draft: StatementLayout[]; setDraft: (value: StatementLayout[]) => void; dateFormats: readonly string[] }) {
  const [layout, setLayout] = useState<StatementLayout | null>(null);
  const [signed, setSigned] = useState(false);
  const [tried, setTried] = useState(false);
  const problem = layout ? layoutProblem(layout) : null;
  const text = (key: keyof StatementLayout, label: string, hint?: string) =>
    layout ? (
      <Field label={label} hint={hint}>
        <input value={layout[key] ?? ""} maxLength={BOUNDS.column} onChange={(event) => setLayout({ ...layout, [key]: event.target.value })} className={INPUT} />
      </Field>
    ) : null;
  return (
    <div className="flex flex-col gap-3">
      <ul aria-label="Layouts" className="m-0 flex list-none flex-col gap-1 p-0 text-md">
        {draft.map((item) => (
          <li key={item.name} className="flex items-center justify-between gap-3">
            <span className="min-w-0">
              {item.label} <span className="text-sm text-muted">{item.name}</span>
            </span>
            <button type="button" className="cursor-pointer text-sm font-bold text-danger underline" onClick={() => setDraft(draft.filter((other) => other.name !== item.name))} aria-label={`Remove ${item.label}`}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      {layout ? (
        <fieldset className="m-0 flex flex-col gap-3 rounded-[12px] border border-border p-3">
          <legend className="px-1 text-sm font-bold">New layout</legend>
          {text("label", "Name", "As staff see it, such as Stanbic business online.")}
          {text("name", "Short name", "Lower case letters, digits and underscores, such as stanbic_csv.")}
          {text("dateColumn", "Date column", "The column heading exactly as it is in the file.")}
          <Field label="How dates are written">
            <select value={layout.dateFormat} onChange={(event) => setLayout({ ...layout, dateFormat: event.target.value })} className={INPUT}>
              {dateFormats.map((format) => (
                <option key={format} value={format}>
                  {format}
                </option>
              ))}
            </select>
          </Field>
          {text("narrative", "Description column")}
          {text("reference", "Reference column (optional)")}
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-1 text-sm font-bold">Amounts</legend>
            <label className="flex items-center gap-2 text-md">
              <input type="radio" checked={!signed} onChange={() => {
                setSigned(false);
                setLayout({ ...layout, amount: null, direction: null, inWord: null, outWord: null, moneyIn: "", moneyOut: "" });
              }} />
              Money in and money out in two columns
            </label>
            <label className="flex items-center gap-2 text-md">
              <input type="radio" checked={signed} onChange={() => {
                setSigned(true);
                setLayout({ ...layout, moneyIn: null, moneyOut: null, amount: "", direction: "", inWord: "", outWord: "" });
              }} />
              One amount column and a column saying which way
            </label>
          </fieldset>
          {signed ? (
            <>
              {text("amount", "Amount column")}
              {text("direction", "Direction column")}
              {text("inWord", "Word for money in", "Such as CR.")}
              {text("outWord", "Word for money out", "Such as DR.")}
            </>
          ) : (
            <>
              {text("moneyIn", "Money in column")}
              {text("moneyOut", "Money out column")}
            </>
          )}
          {tried && problem ? <p className="m-0 text-xs font-bold text-danger">{problem}</p> : null}
          <div className="flex gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setTried(true);
                if (problem) return;
                const tidy = (value: string | null) => (value && value.trim().length > 0 ? value.trim() : null);
                setDraft([
                  ...draft,
                  {
                    ...layout,
                    label: layout.label.trim(),
                    dateColumn: layout.dateColumn.trim(),
                    narrative: layout.narrative.trim(),
                    reference: tidy(layout.reference),
                    moneyIn: tidy(layout.moneyIn),
                    moneyOut: tidy(layout.moneyOut),
                    amount: tidy(layout.amount),
                    direction: tidy(layout.direction),
                    inWord: tidy(layout.inWord),
                    outWord: tidy(layout.outWord),
                  },
                ]);
                setLayout(null);
                setTried(false);
              }}
            >
              Add the layout
            </Button>
            <Button variant="secondary" onClick={() => setLayout(null)}>
              Cancel
            </Button>
          </div>
        </fieldset>
      ) : draft.length < BOUNDS.layouts ? (
        <div>
          <Button variant="secondary" onClick={() => { setSigned(false); setLayout(EMPTY_LAYOUT); }}>
            Add a layout
          </Button>
        </div>
      ) : null}
    </div>
  );
}
