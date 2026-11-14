import { useState } from "react";
import { ROLE_LABELS, ROLES, type Role } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import type { Change, Decision, Member, NewChange, RolePermissions } from "./api.ts";
import { accessLabel, describeChange, formatTime, KIND_LABELS, permissionRows, secondSteps, STATUS_LABELS, type AccessTone } from "./labels.ts";

export const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const roleLabel = (role: Role) => ROLE_LABELS[role];

function ReasonField({
  label,
  value,
  onChange,
  invalid,
  hint = "The second admin reads this, and it goes in the audit trail.",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  invalid: boolean;
  hint?: string;
}) {
  return (
    <label className="flex flex-col gap-1.5 text-sm font-bold">
      {label}
      <span className="text-xs font-medium text-muted">{hint}</span>
      <textarea value={value} rows={2} aria-invalid={invalid} onChange={(event) => onChange(event.target.value)} className={INPUT} />
      {invalid ? <span className="text-xs font-bold text-danger">Write a short reason for the audit trail.</span> : null}
    </label>
  );
}

/** A change waiting for a second admin, with what the reader may do about it. */
export function ChangeCard({
  change,
  canChange,
  busy,
  error,
  onDecide,
}: {
  change: Change;
  canChange: boolean;
  busy: boolean;
  error: string | null;
  onDecide: (decision: Decision, reason: string) => Promise<boolean>;
}) {
  const [deciding, setDeciding] = useState<Decision | null>(null);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const title = describeChange(change, roleLabel);
  const mayDecide = canChange && !change.yours && !change.aboutYou;
  return (
    <article aria-label={title} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-2xs font-extrabold tracking-[0.6px] text-muted uppercase">{KIND_LABELS[change.kind]}</span>
          <h3 className="m-0 text-[17px] font-extrabold">{title}</h3>
        </div>
        <span className="shrink-0 rounded-pill bg-warning-surface px-2 py-0.5 text-2xs font-bold text-warning-text">Waiting</span>
      </div>
      <p className="m-0 text-md leading-normal">“{change.reason}”</p>
      <p className="m-0 text-sm text-muted">
        Proposed by {change.yours ? "you" : change.proposedByName}, {formatTime(change.proposedAt)}
      </p>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      {deciding ? (
        <form
          aria-label={deciding === "approved" ? "Approve the change" : deciding === "rejected" ? "Reject the change" : "Withdraw the change"}
          className="flex flex-col gap-3 border-t border-divider pt-3"
          onSubmit={(event) => {
            event.preventDefault();
            setTried(true);
            if (!reason.trim()) return;
            void onDecide(deciding, reason).then((done) => {
              if (done) setDeciding(null);
            });
          }}
        >
          <ReasonField
            label={deciding === "approved" ? "Why approve it?" : deciding === "rejected" ? "Why reject it?" : "Why withdraw it?"}
            value={reason}
            onChange={setReason}
            invalid={tried && !reason.trim()}
            hint="It goes in the audit trail with your decision."
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant={deciding === "approved" ? "primary" : "secondary"} disabled={busy}>
              {busy ? "Sending" : deciding === "approved" ? "Approve the change" : deciding === "rejected" ? "Reject the change" : "Withdraw the change"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setDeciding(null)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : mayDecide ? (
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setDeciding("approved")}>Approve</Button>
          <Button variant="secondary" onClick={() => setDeciding("rejected")}>
            Reject
          </Button>
        </div>
      ) : change.yours && canChange ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" onClick={() => setDeciding("withdrawn")}>
            Withdraw
          </Button>
          <p className="m-0 text-xs text-muted">You proposed this, so another admin approves it.</p>
        </div>
      ) : change.aboutYou ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3 py-2.5 text-sm text-muted">This change is about you, so another admin decides it.</p>
      ) : null}
    </article>
  );
}

const ACCESS_TONES: Record<AccessTone, string> = {
  good: "text-success",
  waiting: "text-warning-text",
  bad: "text-danger",
  quiet: "text-muted",
};

/** Whether Cloudflare Access, in front of the console, lets this person through. */
function AccessLine({ member }: { member: Member }) {
  const label = accessLabel(member);
  return (
    <span className={cn("mt-0.5 flex items-center gap-1.5 text-xs font-bold", ACCESS_TONES[label.tone])}>
      <span aria-hidden="true" className="inline-block size-1.5 shrink-0 rounded-full bg-current" />
      <span>
        <span className="sr-only">Cloudflare Access: </span>
        {label.text}
      </span>
    </span>
  );
}

type Action = "change_role" | "deactivate" | "reactivate" | "reset_sign_in";

/** One person on the team, and, for an admin, a form to propose a change to them. */
export function MemberRow({
  member,
  isMe,
  canChange,
  waiting,
  busy,
  error,
  onPropose,
  onSyncAccess,
}: {
  member: Member;
  isMe: boolean;
  canChange: boolean;
  /** A change for this person is already waiting. */
  waiting: boolean;
  busy: boolean;
  error: string | null;
  onPropose: (change: NewChange) => Promise<boolean>;
  /** Ask for Cloudflare Access to be brought in line again. */
  onSyncAccess: () => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [action, setAction] = useState<Action>(member.status === "disabled" ? "reactivate" : "change_role");
  const [role, setRole] = useState<Role>(member.role);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const actions: Action[] = member.status === "disabled" ? ["reactivate"] : ["change_role", "deactivate", "reset_sign_in"];
  const sameRole = action === "change_role" && role === member.role;
  const access = accessLabel(member);
  const mayAskAgain = canChange && (member.access === null || member.access.state === "failed");
  return (
    <li aria-label={member.name} className="flex flex-col gap-3 border-b border-divider py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 lg:grid lg:grid-cols-[minmax(200px,2fr)_110px_100px_minmax(170px,1.4fr)_minmax(150px,1.2fr)_170px]">
        <div className="flex min-w-[200px] flex-[2_1_220px] flex-col">
          <span className="text-md font-bold">
            {member.name}
            {isMe ? <span className="font-medium text-muted"> (you)</span> : null}
          </span>
          <span className="truncate text-sm text-muted">{member.email}</span>
          <AccessLine member={member} />
        </div>
        <span className="flex-[1_1_110px] text-sm font-bold">{ROLE_LABELS[member.role]}</span>
        <span
          className={cn(
            "w-fit shrink-0 rounded-pill px-2 py-0.5 text-2xs font-bold",
            member.status === "active" ? "bg-success-surface text-success" : member.status === "invited" ? "bg-warning-surface text-warning-text" : "bg-surface-sunken text-muted",
          )}
        >
          {STATUS_LABELS[member.status]}
        </span>
        <span className="flex-[1_1_180px] text-sm text-muted">{secondSteps(member)}</span>
        <span className="flex-[1_1_150px] text-sm text-muted">
          {member.lastSignedInAt ? `Signed in ${formatTime(member.lastSignedInAt)}` : "Never signed in"}
        </span>
        <span className="lg:justify-self-end">
          {canChange && !isMe && member.status !== "invited" ? (
            waiting ? (
              <span className="text-xs font-semibold text-muted">Change waiting</span>
            ) : (
              <Button variant="secondary" aria-expanded={open} onClick={() => setOpen(!open)}>
                Propose a change
              </Button>
            )
          ) : null}
        </span>
      </div>
      {access.detail || mayAskAgain || (error && !open) ? (
        <div className="flex max-w-[720px] flex-col gap-2">
          {access.detail || mayAskAgain ? (
            <div className={cn("flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md px-3 py-2", access.tone === "bad" ? "bg-danger-surface" : "bg-surface-sunken")}>
              {access.detail ? <p className={cn("m-0 flex-[1_1_260px] text-sm", access.tone === "bad" ? "font-semibold text-danger" : "text-muted")}>{access.detail}</p> : null}
              {mayAskAgain ? (
                <Button variant="secondary" disabled={busy} onClick={() => void onSyncAccess()}>
                  {member.access === null ? "Check Access now" : "Try Access again"}
                </Button>
              ) : null}
            </div>
          ) : null}
          {error && !open ? (
            <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
      {open && !waiting ? (
        <form
          aria-label={`Propose a change for ${member.name}`}
          className="flex max-w-[560px] flex-col gap-3 rounded-lg bg-surface-sunken p-3"
          onSubmit={(event) => {
            event.preventDefault();
            setTried(true);
            if (!reason.trim() || sameRole) return;
            const change: NewChange =
              action === "change_role" ? { kind: action, staffId: member.id, role, reason } : { kind: action, staffId: member.id, reason };
            void onPropose(change).then((done) => {
              if (done) setOpen(false);
            });
          }}
        >
          <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0">
            <legend className="mb-1.5 text-sm font-bold">What should change?</legend>
            {actions.map((choice) => (
              <label key={choice} className="flex items-center gap-1.5 text-sm font-semibold">
                <input type="radio" name={`action-${member.id}`} checked={action === choice} onChange={() => setAction(choice)} />
                {KIND_LABELS[choice]}
              </label>
            ))}
          </fieldset>
          {action === "change_role" ? (
            <label className="flex flex-col gap-1.5 text-sm font-bold">
              New role
              <select value={role} onChange={(event) => setRole(event.target.value as Role)} className={INPUT}>
                {ROLES.map((option) => (
                  <option key={option} value={option}>
                    {ROLE_LABELS[option]}
                  </option>
                ))}
              </select>
              {tried && sameRole ? <span className="text-xs font-bold text-danger">They already hold this role. Choose another.</span> : null}
            </label>
          ) : null}
          <ReasonField label="Why?" value={reason} onChange={setReason} invalid={tried && !reason.trim()} />
          {error ? (
            <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
              {error}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Sending" : "Send for approval"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </li>
  );
}

export function InviteForm({ busy, error, onPropose, onCancel }: { busy: boolean; error: string | null; onPropose: (change: NewChange) => Promise<boolean>; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("support");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const badEmail = !/^[^@\s]+@[^@\s]+$/.test(email.trim());
  return (
    <form
      aria-label="Invite someone"
      className="flex max-w-[560px] flex-col gap-3 rounded-xl border border-border bg-surface p-4"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (!name.trim() || badEmail || !reason.trim()) return;
        void onPropose({ kind: "invite", name: name.trim(), email: email.trim().toLowerCase(), role, reason });
      }}
    >
      <h3 className="m-0 text-[17px] font-extrabold">Invite someone</h3>
      <p className="m-0 text-sm text-muted">Once another admin approves, they get a link by email to set a password and a second step.</p>
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        Full name
        <input value={name} aria-invalid={tried && !name.trim()} onChange={(event) => setName(event.target.value)} className={INPUT} />
        {tried && !name.trim() ? <span className="text-xs font-bold text-danger">Enter their name.</span> : null}
      </label>
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        Work email
        <input type="email" value={email} aria-invalid={tried && badEmail} onChange={(event) => setEmail(event.target.value)} className={INPUT} />
        {tried && badEmail ? <span className="text-xs font-bold text-danger">Enter an email address, such as name@finvestug.com.</span> : null}
      </label>
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        Role
        <select value={role} onChange={(event) => setRole(event.target.value as Role)} className={INPUT}>
          {ROLES.map((option) => (
            <option key={option} value={option}>
              {ROLE_LABELS[option]}
            </option>
          ))}
        </select>
      </label>
      <ReasonField label="Why?" value={reason} onChange={setReason} invalid={tried && !reason.trim()} />
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Sending" : "Send for approval"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** Which role may open which screen and do which action. */
export function RoleMatrix({ roles }: { roles: readonly RolePermissions[] }) {
  const rows = permissionRows();
  const holds = new Map(roles.map((entry) => [entry.role, new Set(entry.permissions)]));
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="What each role can open and do">
      <table className="w-full min-w-[560px] border-collapse text-sm">
        <caption className="sr-only">What each role can open and do</caption>
        <thead>
          <tr className="border-b border-divider">
            <th scope="col" className="px-3 py-2.5 text-left font-extrabold">
              Screen or action
            </th>
            {roles.map((entry) => (
              <th key={entry.role} scope="col" className="px-2 py-2.5 text-center font-extrabold">
                {ROLE_LABELS[entry.role]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.permission} className="border-b border-divider last:border-b-0">
              <th scope="row" className={cn("px-3 py-2 text-left", row.action ? "pl-7 font-medium text-muted" : "font-bold")}>
                {row.label}
              </th>
              {roles.map((entry) => {
                const yes = holds.get(entry.role)?.has(row.permission) ?? false;
                return (
                  <td key={entry.role} className="px-2 py-2 text-center">
                    <span aria-label={yes ? "Yes" : "No"} className={yes ? "font-extrabold text-success" : "text-muted"}>
                      {yes ? "✓" : "–"}
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
