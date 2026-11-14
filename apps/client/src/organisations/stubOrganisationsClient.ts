import { checkReceipt } from "@/payments/receipt";

import { checkName, checkRegistration, DOCUMENTS, MAX_ROLE, problemsOf } from "./rules";
import {
  Application,
  MAX_SIGNATORIES,
  Organisation,
  OrganisationError,
  OrganisationsClient,
  Signatory,
} from "./types";

/**
 * An in-memory stand-in for the company and group routes. Nothing here is real.
 * `OrganisationsProvider.tsx` keeps it out of production builds.
 *
 * The two demo accounts start with the canvas's group, Kira Road Savings
 * Group, with one person joined and one still to answer, and an invitation to
 * sign for a company. An account made by signing up starts with none. The
 * stub finds people by the numbers in PEOPLE. A number ending in 99 cannot
 * reach the service. It refuses what the backend refuses.
 */

const SEEDED = new Set(["+256772123456", "+256701000200"]);
const ME = { clientId: "me", name: "Amara N.", accountNumber: "1001004827" };
const PEOPLE: { match: string[]; clientId: string; name: string; accountNumber: string }[] = [
  { match: ["1001004901", "+256772410001"], clientId: "peter", name: "Peter O.", accountNumber: "1001004901" },
  { match: ["1001004902", "+256752410002"], clientId: "sarah", name: "Sarah A.", accountNumber: "1001004902" },
  { match: ["1001004903", "+256702410003"], clientId: "brian", name: "Brian K.", accountNumber: "1001004903" },
];

function phoneOf(value: string): string | null {
  const digits = value.replace(/[\s().-]/g, "");
  if (/^0\d{9}$/.test(digits)) return `+256${digits.slice(1)}`;
  if (/^256\d{9}$/.test(digits)) return `+${digits}`;
  if (/^\+\d{9,15}$/.test(digits)) return digits;
  return null;
}

const invalid = (field: string, code: string) => new OrganisationError("invalid", [{ field, code }]);

function person(
  who: { clientId: string; name: string; accountNumber: string },
  role: string,
  status: Signatory["status"],
  isApplicant = false,
): Signatory {
  return {
    ...who,
    role,
    isApplicant,
    status,
    invitedAt: "2026-11-12T08:30:00Z",
    answeredAt: status === "invited" ? null : "2026-11-12T10:05:00Z",
  };
}

function settle(found: Organisation): Organisation {
  const joined = found.signatories.filter((s) => s.status === "joined").length;
  const invited = found.signatories.filter((s) => s.status === "invited").length;
  const problems =
    found.status === "draft" && found.isApplicant
      ? problemsOf({
          kind: found.kind,
          registrationNumber: found.registrationNumber,
          approvalsNeeded: found.approvalsNeeded,
          joined,
          invited,
          documents: found.documents.map((d) => d.kind),
        })
      : [];
  return { ...found, problems, documentsNeeded: DOCUMENTS[found.kind] };
}

function seeded(): Organisation[] {
  return [
    settle({
      id: "org-kira-road",
      kind: "group",
      name: "Kira Road Savings Group",
      registrationNumber: null,
      approvalsNeeded: 2,
      status: "draft",
      createdAt: "2026-11-12T08:20:00Z",
      submittedAt: null,
      returnNote: null,
      decidedAt: null,
      accountNumber: null,
      documentsNeeded: [],
      documents: [
        { kind: "constitution", contentType: "application/pdf", byteSize: 482_113, uploadedAt: "2026-11-12T08:40:00Z" },
      ],
      problems: [],
      signatories: [
        person(ME, "Chairperson", "joined", true),
        person(PEOPLE[0]!, "Treasurer", "joined"),
        person(PEOPLE[1]!, "Secretary", "invited"),
      ],
      isApplicant: true,
      invited: false,
    }),
    settle({
      id: "org-nakato-traders",
      kind: "company",
      name: "Nakato Traders Ltd",
      registrationNumber: "80020001234567",
      approvalsNeeded: 1,
      status: "draft",
      createdAt: "2026-11-13T14:00:00Z",
      submittedAt: null,
      returnNote: null,
      decidedAt: null,
      accountNumber: null,
      documentsNeeded: [],
      documents: [],
      problems: [],
      signatories: [
        person(PEOPLE[2]!, "Managing director", "joined", true),
        person(ME, "Director", "invited"),
      ],
      isApplicant: false,
      invited: true,
    }),
  ];
}

export function createStubOrganisationsClient(account: string | null): OrganisationsClient {
  const unreachable = account?.endsWith("99") ?? false;
  let items: Organisation[] = account !== null && SEEDED.has(account) ? seeded() : [];
  let next = 1;

  const reach = async () => {
    await new Promise((resolve) => setTimeout(resolve, 150));
    if (unreachable) throw new OrganisationError("unavailable");
  };
  const find = (id: string) => {
    const found = items.find((o) => o.id === id);
    if (!found) throw new OrganisationError("not_found");
    return found;
  };
  const save = (found: Organisation) => {
    const settled = settle(found);
    items = items.map((o) => (o.id === found.id ? settled : o));
    return settled;
  };
  const draftOf = (id: string) => {
    const found = find(id);
    if (!found.isApplicant) throw new OrganisationError("refused");
    if (found.status !== "draft") throw new OrganisationError("conflict", [{ field: "status", code: `already_${found.status}` }]);
    return found;
  };
  const role = (value: string) => {
    const cleaned = value.trim().split(/\s+/).join(" ");
    if (!cleaned || cleaned.length > MAX_ROLE) throw invalid("role", "required");
    return cleaned;
  };

  return {
    async list() {
      await reach();
      return items;
    },
    async get(id) {
      await reach();
      return find(id);
    },
    async apply(application: Application) {
      await reach();
      const name = checkName(application.name);
      if ("problem" in name) throw invalid("name", name.problem);
      const registration = checkRegistration(application.registrationNumber ?? "");
      if (registration === "invalid") throw invalid("registration_number", "not_a_registration_number");
      const created = settle({
        id: `org-${next++}`,
        kind: application.kind,
        name: name.name,
        registrationNumber: registration,
        approvalsNeeded: application.approvalsNeeded,
        status: "draft",
        createdAt: new Date().toISOString(),
        submittedAt: null,
        returnNote: null,
        decidedAt: null,
        accountNumber: null,
        documentsNeeded: [],
        documents: [],
        problems: [],
        signatories: [person(ME, role(application.role), "joined", true)],
        isApplicant: true,
        invited: false,
      });
      items = [created, ...items];
      return created;
    },
    async change(id, changes) {
      await reach();
      const found = draftOf(id);
      let { name, registrationNumber, approvalsNeeded } = found;
      if (changes.name !== undefined) {
        const checked = checkName(changes.name);
        if ("problem" in checked) throw invalid("name", checked.problem);
        name = checked.name;
      }
      if (changes.registrationNumber !== undefined) {
        const checked = checkRegistration(changes.registrationNumber ?? "");
        if (checked === "invalid") throw invalid("registration_number", "not_a_registration_number");
        registrationNumber = checked;
      }
      if (changes.approvalsNeeded !== undefined) {
        if (changes.approvalsNeeded < 1 || changes.approvalsNeeded > MAX_SIGNATORIES) {
          throw invalid("approvals_needed", "out_of_range");
        }
        approvalsNeeded = changes.approvalsNeeded;
      }
      return save({ ...found, name, registrationNumber, approvalsNeeded });
    },
    async invite(id, to, asRole) {
      await reach();
      const found = draftOf(id);
      const digits = to.replace(/\s/g, "");
      const key = /^\d{10}$/.test(digits) && !digits.startsWith("0") ? digits : phoneOf(to);
      if (!key) throw invalid("to", "no_such_client");
      if (key === ME.accountNumber || key === account) throw invalid("to", "already_a_signatory");
      const who = PEOPLE.find((p) => p.match.includes(key));
      if (!who) throw invalid("to", "no_such_client");
      const existing = found.signatories.find((s) => s.clientId === who.clientId);
      if (existing && (existing.status === "joined" || existing.status === "invited")) {
        throw invalid("to", "already_a_signatory");
      }
      const live = found.signatories.filter((s) => s.status === "joined" || s.status === "invited");
      if (live.length >= MAX_SIGNATORIES) throw invalid("to", "too_many_signatories");
      const added = { ...person(who, role(asRole), "invited"), invitedAt: new Date().toISOString() };
      const signatories = existing
        ? found.signatories.map((s) => (s.clientId === who.clientId ? added : s))
        : [...found.signatories, added];
      return save({ ...found, signatories });
    },
    async remove(id, clientId) {
      await reach();
      const found = draftOf(id);
      const target = found.signatories.find((s) => s.clientId === clientId);
      if (!target) throw new OrganisationError("not_found");
      if (target.isApplicant) throw invalid("client_id", "applicant_stays");
      return save({
        ...found,
        signatories: found.signatories.map((s) => (s.clientId === clientId ? { ...s, status: "removed" as const } : s)),
      });
    },
    async answer(id, join) {
      await reach();
      const found = find(id);
      if (!found.invited) throw new OrganisationError("conflict", [{ field: "signatory", code: "not_invited" }]);
      const status = join ? ("joined" as const) : ("declined" as const);
      const answered = {
        ...found,
        invited: false,
        signatories: found.signatories.map((s) =>
          s.clientId === ME.clientId ? { ...s, status, answeredAt: new Date().toISOString() } : s,
        ),
      };
      if (!join) {
        // Someone who declines no longer sees it.
        items = items.filter((o) => o.id !== id);
        return settle(answered);
      }
      return save(answered);
    },
    async sendDocument(id, kind, file) {
      await reach();
      const found = draftOf(id);
      if (!DOCUMENTS[found.kind].includes(kind)) throw invalid("kind", "not_for_this_kind");
      const checked = checkReceipt({ uri: file.uri, name: file.name, mimeType: file.mimeType, size: file.sizeBytes });
      if (typeof checked === "string") {
        throw invalid("document", checked === "wrong_type" ? "unsupported_content_type" : checked);
      }
      const document = { kind, contentType: checked.mimeType, byteSize: checked.sizeBytes, uploadedAt: new Date().toISOString() };
      return save({ ...found, documents: [...found.documents.filter((d) => d.kind !== kind), document] });
    },
    async submit(id) {
      await reach();
      const found = draftOf(id);
      if (found.problems.length) {
        throw new OrganisationError(
          "not_ready",
          found.problems.map((code) => ({ field: "problems", code })),
        );
      }
      return save({ ...found, status: "submitted", submittedAt: new Date().toISOString(), returnNote: null });
    },
  };
}
