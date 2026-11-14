import type { ClientOrganisationOut } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";
import { uploadable } from "@/payments/apiPaymentsClient";

import { Organisation, OrganisationError, OrganisationsClient } from "./types";

/** The company and group account screens' contract, answered by the routes in docs/organisations.md. */

export function toOrganisation(view: ClientOrganisationOut): Organisation {
  return {
    id: view.id,
    kind: view.kind,
    name: view.name,
    registrationNumber: view.registration_number,
    approvalsNeeded: view.approvals_needed,
    status: view.status,
    createdAt: view.created_at,
    submittedAt: view.submitted_at,
    returnNote: view.return_note,
    decidedAt: view.decided_at,
    accountNumber: view.account_number,
    documentsNeeded: view.documents_needed,
    documents: view.documents.map((d) => ({
      kind: d.kind,
      contentType: d.content_type,
      byteSize: d.byte_size,
      uploadedAt: d.uploaded_at,
    })),
    problems: view.problems,
    signatories: view.signatories.map((s) => ({
      clientId: s.client_id,
      role: s.role,
      name: s.name,
      accountNumber: s.account_number,
      isApplicant: s.is_applicant,
      status: s.status,
      invitedAt: s.invited_at,
      answeredAt: s.answered_at,
    })),
    isApplicant: view.is_applicant,
    invited: view.invited,
  };
}

/** Turns an API refusal into what the screens understand. Nothing else is shown. */
export function translate(error: unknown): OrganisationError {
  if (error instanceof OrganisationError) return error;
  if (error instanceof ApiError) {
    const fields = error.fields.map((f) => ({ field: f.field.replace(/^body\./, ""), code: f.code }));
    switch (error.code) {
      case "validation_failed":
        return new OrganisationError("invalid", fields);
      case "conflict":
        if (fields.some((f) => f.field === "problems")) return new OrganisationError("not_ready", fields);
        if (fields.some((f) => f.code === "not_verified" || f.code === "signatory_not_verified")) {
          return new OrganisationError("not_verified", fields);
        }
        return new OrganisationError("conflict", fields);
      case "not_found":
        return new OrganisationError("not_found");
      case "rate_limited":
        return new OrganisationError("rate_limited");
      // Offline and timed out never reached an answer.
      case "offline":
      case "timeout":
      case "bad_response":
        return new OrganisationError("unavailable");
      default:
        return new OrganisationError("refused");
    }
  }
  return new OrganisationError("unavailable");
}

async function wrap(work: () => Promise<ClientOrganisationOut>): Promise<Organisation> {
  try {
    return toOrganisation(await work());
  } catch (error) {
    throw translate(error);
  }
}

export function createApiOrganisationsClient(): OrganisationsClient {
  const of = (id: string) => ({ organisation_id: id });
  return {
    async list() {
      try {
        return (await api("GET /client/organisations", {})).items.map(toOrganisation);
      } catch (error) {
        throw translate(error);
      }
    },
    get: (id) => wrap(() => api("GET /client/organisations/{organisation_id}", { params: of(id) })),
    apply: (application) =>
      wrap(() =>
        api("POST /client/organisations", {
          body: {
            kind: application.kind,
            name: application.name,
            registration_number: application.registrationNumber,
            approvals_needed: application.approvalsNeeded,
            role: application.role,
          },
        }),
      ),
    change: (id, changes) =>
      wrap(() =>
        api("PATCH /client/organisations/{organisation_id}", {
          params: of(id),
          body: {
            name: changes.name,
            registration_number: changes.registrationNumber,
            approvals_needed: changes.approvalsNeeded,
          },
        }),
      ),
    invite: (id, to, role) =>
      wrap(() => api("POST /client/organisations/{organisation_id}/signatories", { params: of(id), body: { to, role } })),
    remove: (id, clientId) =>
      wrap(() =>
        api("DELETE /client/organisations/{organisation_id}/signatories/{client_id}", {
          params: { organisation_id: id, client_id: clientId },
        }),
      ),
    answer: (id, join) =>
      wrap(() => api("POST /client/organisations/{organisation_id}/answer", { params: of(id), body: { join } })),
    sendDocument: (id, kind, file) =>
      wrap(async () =>
        api("PUT /client/organisations/{organisation_id}/documents/{kind}", {
          params: { organisation_id: id, kind },
          body: { document: await uploadable(file) },
          // A 10 MB file on a slow connection takes a while.
          timeoutMs: 120_000,
        }),
      ),
    submit: (id) => wrap(() => api("POST /client/organisations/{organisation_id}/submission", { params: of(id) })),
  };
}
