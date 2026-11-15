/**
 * What an audit event's action code means, in words, such as "investments.run_request.approved"
 * read as "Request to run interest again approved".
 *
 * The trail stores codes so it can be filtered and exported. Staff read words. Codes are made by
 * each module, some with a step on the end (`payments.reversal_done`), so a code nobody has named
 * here is still read from its parts: the module, the thing, then what happened to it. The code
 * itself stays on the event's details.
 */

/** Whole codes whose words do not come out well from their parts. */
const NAMED: Record<string, string> = {
  "audit.trail_exported": "Audit trail exported",
  "autoinvest.runs_viewed": "Automatic investing runs opened",
  "bonds.holding_started": "Bond holding started",
  "bonds.purchase_requested": "Bond purchase asked for",
  "client_data.exported": "Client's own data exported",
  "complaints.register_exported": "Complaints register exported",
  "compliance.register_exported": "Compliance register exported",
  "console.client.note_written": "Staff note written on a client",
  "dealing.buffer_capital.in_proposed": "Paying in Finvest's own money proposed",
  "dealing.buffer_capital.in_approved": "Paying in Finvest's own money approved",
  "dealing.buffer_capital.in_declined": "Paying in Finvest's own money declined",
  "dealing.buffer_capital.out_proposed": "Taking out Finvest's own money proposed",
  "dealing.buffer_capital.out_approved": "Taking out Finvest's own money approved",
  "dealing.buffer_capital.out_declined": "Taking out Finvest's own money declined",
  "dealing.withdrawal.funded_from_sale": "Early withdrawal paid for by selling a bill",
  "finance.month_closed": "Month closed",
  "finance.journal_exported": "Journal exported for the accounts",
  "identity.client.frozen": "Client froze their account",
  "identity.client.claimed": "Client claimed their carried-over account",
  "identity.phone.changed_by_client": "Client changed their mobile number",
  "identity.session.opened": "Signed in",
  "identity.session.reconfirmed": "Confirmed it was them",
  "identity.session.ended_after_wrong_pins": "Signed out after wrong PINs",
  "identity.session.signed_out_elsewhere": "Signed out on another device",
  "identity.session.signed_out_other_devices": "Signed out of every other device",
  "identity.staff.enrolment_link_issued": "Staff set-up link issued",
  "identity.staff.enrolled": "Staff member finished set-up",
  "identity.team.access_sync_requested": "Staff access check asked for",
  "investments.maturity_chosen": "Choice at maturity made",
  "investments.positions_exported": "Investment positions exported",
  "investments.withdrawal_requested": "Early withdrawal asked for",
  "investments.withdrawn_early": "Investment withdrawn early",
  "kyc.check.document_number_shown": "ID number shown",
  "kyc.check.photos_viewed": "Identity check photos opened",
  "kyc.check.photos_requested": "New identity photos asked for",
  "kyc.check.sent_to_review": "Identity check sent for a person to decide",
  "kyc.next_of_kin.viewed": "Next of kin opened",
  "kyc.phone_name.read": "Name on the mobile number read",
  "kyc.risk_answers.given": "Investing questions answered",
  "ledger.day_closed": "Day closed",
  "organisation.documents_viewed": "Company documents opened",
  "payouts.bank_batch_file_downloaded": "Bank payout file downloaded",
  "reconciliation.statement_imported": "Statement imported",
  "reports.downloaded": "Report downloaded",
  "screening.rescreen_requested": "Screening asked for again",
  "tax.month_exported": "Tax month exported",
  "tax.month_viewed": "Tax month opened",
};

/** What each module's actions are about, when the code names nothing more. */
const MODULES: Record<string, string> = {
  audit: "Audit trail",
  autoinvest: "Automatic investing",
  bonds: "Bond",
  client_data: "Client data",
  closure: "Account closure",
  complaints: "Complaint",
  compliance: "Compliance case",
  console: "Client",
  dealing: "Dealing",
  documents: "Document",
  finance: "Finance",
  goals: "Goal",
  identity: "Account",
  investments: "Investment",
  kyc: "Identity check",
  ledger: "Ledger",
  migration: "Migration",
  notifications: "Notification",
  organisation: "Company account",
  organisations: "Company account",
  payments: "Payment",
  payouts: "Payout",
  reconciliation: "Reconciliation",
  reports: "Report",
  screening: "Screening",
  settings: "Setting",
  tax: "Tax",
};

/** The things actions happen to, by module and thing. */
const SUBJECTS: Record<string, string> = {
  "autoinvest.plan": "Automatic plan",
  "bonds.change": "Bond change",
  "complaints.outcome": "Complaint outcome",
  "compliance.case": "Compliance case",
  "console.client_action": "Client action",
  "dealing.auction": "Auction date",
  "dealing.buffer_capital": "Finvest's own buffer money",
  "dealing.buffer_rules": "Buffer rules change",
  "dealing.order": "Dealing order",
  "documents.note_reissue": "Note reissue",
  "finance.code_map": "Accounting codes change",
  "finance.month_close": "Month close",
  "identity.authenticator": "Authenticator app",
  "identity.client": "Client account",
  "identity.email": "Email address",
  "identity.passkey": "Passkey",
  "identity.password": "Password",
  "identity.pin": "App PIN",
  "identity.session": "Sign-in",
  "identity.staff": "Staff member",
  "identity.staff_access": "Staff access",
  "identity.team": "Team change",
  "identity.unlock_key": "Unlock key",
  "investments.booking": "Booking for a client",
  "investments.correction": "Investment correction",
  "investments.product": "Product change",
  "investments.rate": "Rate change",
  "investments.run_request": "Request to run interest again",
  "investments.transfer": "Investment transfer",
  "kyc.check": "Identity check",
  "kyc.face_confirmation": "Selfie confirmation",
  "kyc.liveness": "Liveness check",
  "kyc.next_of_kin": "Next of kin",
  "ledger.correction": "Ledger correction",
  "migration.carry_resolution": "Carry-over fix",
  "migration.claim_check": "Claim check",
  "migration.claim_link": "Claim link",
  "migration.control_totals": "Control totals",
  "migration.identity_check": "Carried-over identity check",
  "migration.sign_off": "Sign-off",
  "migration.switch": "Switch",
  "notifications.announcement": "Announcement",
  "notifications.suppression_lift": "Email suppression lift",
  "notifications.wording": "Client message wording",
  "organisation.signatory": "Signatory",
  "organisation.document": "Company document",
  "payments.bank_account": "Pay-in bank account change",
  "payments.bank_match": "Bank transfer match",
  "payments.bank_transfer": "Bank transfer",
  "payments.deposit": "Deposit",
  "payments.provider_refund": "Provider refund",
  "payments.reassignment": "Payment move to another client",
  "payments.receivable": "Amount owed by a provider",
  "payments.reversal": "Payment reversal",
  "payouts.limits": "Withdrawal limits change",
  "tax.remittance": "Tax payment to URA",
};

/** Steps a code may end with after an underscore, as in `payments.reversal_done`. */
const STEPS = [
  "approved",
  "proposed",
  "prepared",
  "declined",
  "rejected",
  "sent_back",
  "withdrawn",
  "cancelled",
  "done",
  "failed",
  "requested",
  "exported",
  "viewed",
  "downloaded",
  "opened",
  "closed",
  "confirmed",
  "refunded",
  "reversed",
  "settled",
  "recorded",
  "changed",
  "decided",
  "entered",
  "sent",
  "lifted",
  "held",
  "released",
  "paid",
  "recovered",
  "written_off",
];

/** How a step reads, when its words are not just the code with spaces. */
const STEP_WORDS: Record<string, string> = {
  sent_back: "sent back",
  done: "carried out",
  requested: "asked for",
  viewed: "opened",
};

function words(code: string): string {
  return code.replace(/_/g, " ").trim();
}

function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The thing a module and part name, as in "Dealing order", or the part read as words. */
function subject(module: string, part: string): string {
  const named = SUBJECTS[`${module}.${part}`];
  if (named) return named;
  const noun = MODULES[module];
  return noun ? `${noun}, ${words(part)}` : words(part);
}

function step(code: string): string {
  return STEP_WORDS[code] ?? words(code);
}

/** The action in words. A code with no known parts is read with its dots and underscores as spaces. */
export function describeAction(code: string): string {
  const named = NAMED[code];
  if (named) return named;
  const parts = code.split(".").filter((part) => part !== "");
  const [module, ...rest] = parts;
  if (!module || rest.length === 0) return sentence(words(code));
  if (rest.length >= 2) {
    const last = rest[rest.length - 1] as string;
    const middle = rest.slice(0, -1).join("_");
    return sentence(`${subject(module, middle)} ${step(last)}`);
  }
  const only = rest[0] as string;
  // `dealing.buffer_capital.in_proposed` and `payments.reversal_done` both end in a step.
  for (const known of STEPS) {
    if (only.endsWith(`_${known}`)) {
      const thing = only.slice(0, -known.length - 1);
      if (SUBJECTS[`${module}.${thing}`] || MODULES[module]) return sentence(`${subject(module, thing)} ${step(known)}`);
    }
  }
  // `complaints.closed`: the module is the thing, and the rest is what happened to it.
  const noun = MODULES[module];
  return sentence(noun ? `${noun} ${step(only)}` : words(`${module} ${only}`));
}

/** Record types whose words do not come out well from the code. */
const RECORD_TYPES: Record<string, string> = {
  kyc_check: "Identity check",
  investment_run_request: "Request to run interest again",
  reconciliation_day: "Day close",
  audit_trail: "Audit trail",
  staff_change: "Team change",
  organisation: "Company account",
};

/** A record type in words, such as "Rate change" for `rate_change`. */
export function describeRecordType(type: string): string {
  return RECORD_TYPES[type] ?? sentence(words(type.replace(/\./g, " ")));
}
