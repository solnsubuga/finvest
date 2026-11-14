/**
 * The contract between the company and group account screens and the
 * backend (docs/organisations.md). A company or group is applied for by one
 * of its people, who adds the others to sign for it and its documents, and
 * hands it in for two members of staff to check and approve.
 */
import { ReceiptFile } from "@/payments/types";

export type OrganisationKind = "company" | "group";

export type OrganisationStatus = "draft" | "submitted" | "checked" | "approved" | "rejected";

export type SignatoryStatus = "invited" | "joined" | "declined" | "removed";

export type DocumentKind =
  | "certificate_of_incorporation"
  | "board_resolution"
  | "constitution"
  | "group_resolution";

/** Why it cannot be handed in yet, in the order to fix them. */
export type Problem =
  | "signatories_waiting"
  | "too_few_signatories"
  | "approvals_more_than_signatories"
  | "approvals_too_few"
  | "documents_missing"
  | "registration_number_missing";

export type Signatory = {
  clientId: string;
  role: string;
  /** As other clients see them, such as "Brian K.". */
  name: string;
  accountNumber: string;
  isApplicant: boolean;
  status: SignatoryStatus;
  invitedAt: string;
  answeredAt: string | null;
};

export type HandedInDocument = {
  kind: DocumentKind;
  contentType: string;
  byteSize: number;
  uploadedAt: string;
};

export type Organisation = {
  id: string;
  kind: OrganisationKind;
  name: string;
  registrationNumber: string | null;
  approvalsNeeded: number;
  status: OrganisationStatus;
  createdAt: string;
  submittedAt: string | null;
  /** What staff asked to change, while it is back with the applicant. */
  returnNote: string | null;
  decidedAt: string | null;
  /** Its own account number, once approved. */
  accountNumber: string | null;
  documentsNeeded: DocumentKind[];
  documents: HandedInDocument[];
  problems: Problem[];
  signatories: Signatory[];
  /** True for the person who applied: only they change it. */
  isApplicant: boolean;
  /** True when the reader is asked to sign and has not answered. */
  invited: boolean;
};

export type Application = {
  kind: OrganisationKind;
  name: string;
  registrationNumber: string | null;
  role: string;
  approvalsNeeded: number;
};

export type OrganisationErrorCode =
  /** The request never reached Finvest, or no answer came back. */
  | "unavailable"
  | "rate_limited"
  /** Finvest refused for a reason the screen has no words of its own for. */
  | "refused"
  /** A field was refused. `fields` says which and why. */
  | "invalid"
  /** It cannot be handed in yet. `fields` lists each problem, under `problems`. */
  | "not_ready"
  /** The reader's own identity check is not approved, or someone signing is no longer. */
  | "not_verified"
  /** It changed meanwhile, such as staff taking it, or the invitation being withdrawn. */
  | "conflict"
  | "not_found";

export class OrganisationError extends Error {
  readonly code: OrganisationErrorCode;
  readonly fields: { field: string; code: string }[];

  constructor(code: OrganisationErrorCode, fields: { field: string; code: string }[] = []) {
    super(code);
    this.name = "OrganisationError";
    this.code = code;
    this.fields = fields;
  }
}

export interface OrganisationsClient {
  list(): Promise<Organisation[]>;
  get(id: string): Promise<Organisation>;
  apply(application: Application): Promise<Organisation>;
  change(
    id: string,
    changes: { name?: string; registrationNumber?: string | null; approvalsNeeded?: number },
  ): Promise<Organisation>;
  /** `to` is a Finvest account number or a mobile number. */
  invite(id: string, to: string, role: string): Promise<Organisation>;
  remove(id: string, clientId: string): Promise<Organisation>;
  answer(id: string, join: boolean): Promise<Organisation>;
  sendDocument(id: string, kind: DocumentKind, file: ReceiptFile): Promise<Organisation>;
  submit(id: string): Promise<Organisation>;
}

export const MAX_SIGNATORIES = 10;
export const MIN_SIGNATORIES: Record<OrganisationKind, number> = { company: 1, group: 2 };
export const MIN_APPROVALS: Record<OrganisationKind, number> = { company: 1, group: 2 };

export const KIND_LABEL: Record<OrganisationKind, string> = {
  company: "Company",
  group: "Group or SACCO",
};

export const DOCUMENT_LABEL: Record<DocumentKind, { title: string; detail: string }> = {
  certificate_of_incorporation: {
    title: "Certificate of incorporation",
    detail: "From URSB, showing the registration number",
  },
  board_resolution: {
    title: "Board resolution",
    detail: "Naming who signs for the company on Finvest",
  },
  constitution: {
    title: "Constitution",
    detail: "PDF or photo, signed by members",
  },
  group_resolution: {
    title: "Group resolution",
    detail: "Minutes naming who signs for the group on Finvest",
  },
};

export const PROBLEM_WORDS: Record<Problem, string> = {
  signatories_waiting: "Someone you asked has not answered yet.",
  too_few_signatories: "Not enough people have joined yet.",
  approvals_more_than_signatories: "The rule asks for more approvals than people have joined.",
  approvals_too_few: "A group needs at least two people to approve money leaving.",
  documents_missing: "A document is still missing.",
  registration_number_missing: "Add the company's registration number.",
};

/** The people still signing: not those who declined or were taken off. */
export function signing(organisation: Organisation): Signatory[] {
  return organisation.signatories.filter((s) => s.status === "invited" || s.status === "joined");
}

/** The withdrawal rules to offer, from the kind's least up to everyone signing. */
export function approvalChoices(organisation: Organisation): { value: number; label: string; detail: string }[] {
  const people = Math.max(signing(organisation).length, MIN_SIGNATORIES[organisation.kind]);
  const least = MIN_APPROVALS[organisation.kind];
  const choices: { value: number; label: string; detail: string }[] = [];
  for (let value = least; value <= Math.max(people, least); value += 1) {
    const all = value === people;
    choices.push({
      value,
      label: all ? (people === 1 ? "The one signatory" : `All ${people} signatories`) : `Any ${value} of ${people} signatories`,
      detail: all ? (people === 1 ? "A sole director" : "Slower, strictest") : value === least ? "Recommended for most" : "",
    });
  }
  return choices;
}

export function describeOrganisationError(error: unknown): string {
  if (error instanceof OrganisationError) {
    switch (error.code) {
      case "rate_limited":
        return "Too many tries. Wait a few minutes and try again.";
      case "not_verified":
        return "Every person signing must have an approved ID check on Finvest. Finish yours first.";
      case "conflict":
        return "Something changed meanwhile. Go back and try again.";
      case "not_found":
        return "We could not find this application. It may have been withdrawn.";
      case "invalid":
        return "Some details need fixing. They are marked below.";
      case "not_ready":
        return "It is not ready to hand in yet. What is missing is listed above.";
      case "refused":
        return "Finvest could not do this just now. Try again later, or ask us in Help.";
      default:
        break;
    }
  }
  return "We could not reach Finvest. Check your connection and try again.";
}
