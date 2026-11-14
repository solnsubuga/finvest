/**
 * The console's view of company and group account applications: handed in by a client, checked
 * by one member of staff and approved by a different one, which opens the account. Either may
 * send it back to the applicant, or reject it. See docs/organisations.md.
 *
 * The shapes follow the staff routes under /staff/organisations. `createHttpOrganisationsApi`
 * calls them; `createStubOrganisationsApi` is the sample for `pnpm dev` and previews.
 */

import type { StaffOrganisationOut } from "@finvest/api-client/staff";
import { ApiError, type Http } from "@/api/http.ts";

export type OrganisationKind = "company" | "group";
export type OrganisationStatus = "draft" | "submitted" | "checked" | "approved" | "rejected";
export type DocumentKind = "certificate_of_incorporation" | "board_resolution" | "constitution" | "group_resolution";
export type Decision = "check" | "approve" | "send_back" | "reject";

export interface Signatory {
  clientId: string;
  role: string;
  /** As clients see each other, such as "Brian K.". */
  shownName: string;
  /** The full name on their approved identity document, or null if it is no longer approved. */
  verifiedName: string | null;
  accountNumber: string;
  isApplicant: boolean;
  status: "invited" | "joined" | "declined" | "removed";
}

export interface Organisation {
  id: string;
  kind: OrganisationKind;
  name: string;
  registrationNumber: string | null;
  approvalsNeeded: number;
  status: OrganisationStatus;
  applicantId: string;
  submittedAt: string | null;
  signatories: Signatory[];
  documentsNeeded: DocumentKind[];
  documents: { kind: DocumentKind; contentType: string; byteSize: number; uploadedAt: string }[];
  check: { by: string | null; byName: string | null; at: string; note: string | null } | null;
  decision: { byName: string | null; at: string; note: string | null } | null;
  accountNumber: string | null;
  /** What this member of staff may do with it now, as the API says. */
  canCheck: boolean;
  canApprove: boolean;
  canSendBack: boolean;
  canReject: boolean;
}

export interface DocumentLink {
  kind: DocumentKind;
  contentType: string;
  url: string;
}

export interface OrganisationsApi {
  /** Every application handed in and not decided yet, oldest first. */
  list(): Promise<Organisation[]>;
  get(id: string): Promise<Organisation>;
  /** Five-minute links to its documents. Each viewing is on the audit trail. */
  documentLinks(id: string): Promise<DocumentLink[]>;
  decide(id: string, decision: Decision, note: string): Promise<Organisation>;
}

export type OrganisationsErrorCode =
  | "invalid"
  | "signatory_not_verified"
  | "already_decided"
  | "forbidden"
  | "not_found"
  | "not_confirmed"
  | "not_connected"
  | "unavailable";

export class OrganisationsError extends Error {
  readonly code: OrganisationsErrorCode;

  constructor(code: OrganisationsErrorCode) {
    super(code);
    this.name = "OrganisationsError";
    this.code = code;
  }
}

const MESSAGES: Record<OrganisationsErrorCode, string> = {
  invalid: "Finvest refused the note. Write one, under 500 characters, and send again.",
  signatory_not_verified:
    "Someone who signs for it no longer has an approved, active Finvest account. Send it back so the applicant can change who signs.",
  already_decided: "Someone else has already moved this application on. It has been reloaded.",
  forbidden: "You are not allowed to do this. A different member of staff must approve an application you checked.",
  not_found: "This application no longer exists.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  not_connected: "Company and group accounts are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function organisationsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof OrganisationsError ? error.code : "unavailable"];
}

export function toOrganisationsError(error: unknown): OrganisationsError {
  if (error instanceof OrganisationsError) return error;
  if (!(error instanceof ApiError)) return new OrganisationsError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new OrganisationsError("invalid");
    case "conflict":
      return new OrganisationsError(error.fieldCode === "signatory_not_verified" ? "signatory_not_verified" : "already_decided");
    case "forbidden":
      return new OrganisationsError("forbidden");
    case "not_found":
      return new OrganisationsError("not_found");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case "confirm_it_is_you":
      return new OrganisationsError("not_confirmed");
    default:
      return new OrganisationsError("unavailable");
  }
}

export function toOrganisation(json: StaffOrganisationOut): Organisation {
  return {
    id: json.id,
    kind: json.kind,
    name: json.name,
    registrationNumber: json.registration_number,
    approvalsNeeded: json.approvals_needed,
    status: json.status,
    applicantId: json.applicant_id,
    submittedAt: json.submitted_at,
    signatories: json.signatories.map((s) => ({
      clientId: s.client_id,
      role: s.role,
      shownName: s.name,
      verifiedName: s.verified_name,
      accountNumber: s.account_number,
      isApplicant: s.is_applicant,
      status: s.status,
    })),
    documentsNeeded: json.documents_needed,
    documents: json.documents.map((d) => ({
      kind: d.kind,
      contentType: d.content_type,
      byteSize: d.byte_size,
      uploadedAt: d.uploaded_at,
    })),
    check: json.checked_at
      ? { by: json.checked_by, byName: json.checked_by_name, at: json.checked_at, note: json.check_note }
      : null,
    decision: json.decided_at ? { byName: json.decided_by_name, at: json.decided_at, note: json.decision_note } : null,
    accountNumber: json.account_number,
    canCheck: json.can_check,
    canApprove: json.can_approve,
    canSendBack: json.can_send_back,
    canReject: json.can_reject,
  };
}

const ACTIONS: Record<Decision, string> = {
  check: "check",
  approve: "approval",
  send_back: "return",
  reject: "rejection",
};

export function createHttpOrganisationsApi(http: Http): OrganisationsApi {
  const path = (id: string, action = "") => `/staff/organisations/${encodeURIComponent(id)}${action}`;

  async function call<T>(run: () => Promise<T>): Promise<T> {
    try {
      return await run();
    } catch (error) {
      throw toOrganisationsError(error);
    }
  }

  return {
    list: () =>
      call(async () => (await http.request<{ items: StaffOrganisationOut[] }>("GET", "/staff/organisations")).items.map(toOrganisation)),
    get: (id) => call(async () => toOrganisation(await http.request<StaffOrganisationOut>("GET", path(id)))),
    documentLinks: (id) =>
      call(async () => {
        const found = await http.request<{ documents: { kind: DocumentKind; content_type: string; url: string }[] }>(
          "POST",
          path(id, "/document-links"),
        );
        return found.documents.map((d) => ({ kind: d.kind, contentType: d.content_type, url: d.url }));
      }),
    decide: (id, decision, note) =>
      call(async () => toOrganisation(await http.request<StaffOrganisationOut>("POST", path(id, `/${ACTIONS[decision]}`), { note }))),
  };
}

export const notConnectedOrganisationsApi: OrganisationsApi = {
  list: () => Promise.reject(new OrganisationsError("not_connected")),
  get: () => Promise.reject(new OrganisationsError("not_connected")),
  documentLinks: () => Promise.reject(new OrganisationsError("not_connected")),
  decide: () => Promise.reject(new OrganisationsError("not_connected")),
};

/**
 * Two sample applications: a group handed in and waiting for its check, and a company checked
 * by a colleague and waiting for a second person. Nothing here is real.
 */
export function createStubOrganisationsApi(
  me: string,
  now: () => number = Date.now,
  sample: "sample" | "empty" | "error" = "sample",
): OrganisationsApi {
  const iso = (minutesAgo: number) => new Date(now() - minutesAgo * 60_000).toISOString();
  const person = (
    clientId: string,
    role: string,
    shownName: string,
    verifiedName: string,
    accountNumber: string,
    isApplicant = false,
  ): Signatory => ({ clientId, role, shownName, verifiedName, accountNumber, isApplicant, status: "joined" });
  let items: Organisation[] =
    sample === "sample"
      ? [
          {
            id: "-NstubOrg00000000001",
            kind: "group",
            name: "Kira Road Savings Group",
            registrationNumber: null,
            approvalsNeeded: 2,
            status: "submitted",
            applicantId: "-NstubClient00000042",
            submittedAt: iso(26 * 60),
            signatories: [
              person("-NstubClient00000042", "Chairperson", "Amara N.", "Amara Nakato", "1001004827", true),
              person("-NstubClient00000051", "Treasurer", "Peter O.", "Peter Okello", "1001004901"),
              person("-NstubClient00000052", "Secretary", "Sarah A.", "Sarah Achieng", "1001004902"),
            ],
            documentsNeeded: ["constitution", "group_resolution"],
            documents: [
              { kind: "constitution", contentType: "application/pdf", byteSize: 482_113, uploadedAt: iso(27 * 60) },
              { kind: "group_resolution", contentType: "image/jpeg", byteSize: 1_204_551, uploadedAt: iso(26 * 60) },
            ],
            check: null,
            decision: null,
            accountNumber: null,
            canCheck: true,
            canApprove: false,
            canSendBack: true,
            canReject: true,
          },
          {
            id: "-NstubOrg00000000002",
            kind: "company",
            name: "Nakato Traders Ltd",
            registrationNumber: "80020001234567",
            approvalsNeeded: 1,
            status: "checked",
            applicantId: "-NstubClient00000061",
            submittedAt: iso(3 * 24 * 60),
            signatories: [
              person("-NstubClient00000061", "Managing director", "Brian K.", "Brian Kato", "1001004903", true),
              person("-NstubClient00000042", "Director", "Amara N.", "Amara Nakato", "1001004827"),
            ],
            documentsNeeded: ["certificate_of_incorporation", "board_resolution"],
            documents: [
              { kind: "certificate_of_incorporation", contentType: "application/pdf", byteSize: 220_118, uploadedAt: iso(3 * 24 * 60 + 20) },
              { kind: "board_resolution", contentType: "application/pdf", byteSize: 98_004, uploadedAt: iso(3 * 24 * 60 + 10) },
            ],
            check: {
              by: "-NstubStaff000000007",
              byName: "Ruth Namata",
              at: iso(90),
              note: "Certificate matches URSB 80020001234567. Resolution names both directors.",
            },
            decision: null,
            accountNumber: null,
            canCheck: false,
            canApprove: true,
            canSendBack: true,
            canReject: true,
          },
        ]
      : [];
  let issued = 3000000000;

  const reach = async () => {
    await new Promise((resolve) => setTimeout(resolve, 120));
    if (sample === "error") throw new OrganisationsError("unavailable");
  };
  const find = (id: string) => {
    const found = items.find((o) => o.id === id);
    if (!found) throw new OrganisationsError("not_found");
    return found;
  };
  const save = (found: Organisation) => {
    items = items.map((o) => (o.id === found.id ? found : o));
    return found;
  };
  const allowed = (status: OrganisationStatus, checkedBy: string | null) => ({
    canCheck: status === "submitted",
    canApprove: status === "checked" && checkedBy !== me,
    canSendBack: status === "submitted" || status === "checked",
    canReject: status === "submitted" || status === "checked",
  });

  return {
    async list() {
      await reach();
      return items.filter((o) => o.status === "submitted" || o.status === "checked");
    },
    async get(id) {
      await reach();
      return find(id);
    },
    async documentLinks(id) {
      await reach();
      return find(id).documents.map((d) => ({ kind: d.kind, contentType: d.contentType, url: "about:blank" }));
    },
    async decide(id, decision, note) {
      await reach();
      const found = find(id);
      if (!note.trim() || note.length > 500) throw new OrganisationsError("invalid");
      const at = new Date(now()).toISOString();
      if (decision === "check") {
        if (!found.canCheck) throw new OrganisationsError("already_decided");
        return save({ ...found, status: "checked", check: { by: me, byName: "You", at, note }, ...allowed("checked", me) });
      }
      if (decision === "approve") {
        if (found.status !== "checked") throw new OrganisationsError("already_decided");
        if (found.check?.by === me) throw new OrganisationsError("forbidden");
        issued += 1;
        const number = `${found.kind === "company" ? "30" : "40"}${String(issued).slice(2)}`;
        return save({
          ...found,
          status: "approved",
          accountNumber: number,
          decision: { byName: "You", at, note },
          ...allowed("approved", found.check?.by ?? null),
        });
      }
      if (!found.canSendBack) throw new OrganisationsError("already_decided");
      if (decision === "send_back") {
        return save({ ...found, status: "draft", check: null, ...allowed("draft", null) });
      }
      return save({ ...found, status: "rejected", decision: { byName: "You", at, note }, ...allowed("rejected", null) });
    },
  };
}
