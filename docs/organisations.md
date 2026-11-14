# Company and group accounts

A company, or a group such as a savings club, association or SACCO, can apply for its own Finvest account. This is the account type choice, the signatories and the business checks (KYB) from the [design canvas](design.md). The module is `backend/app/modules/organisations`, which owns `organisations`, `organisation_signatories` and `organisation_documents` (migration `0242_organisations`).

**An organisation cannot invest yet.** Opening one gives it an account number and a record of who signs for it, and moves no money. Whether money pooled by a group counts as a collective scheme is open with counsel (see [decisions.md](decisions.md)), and investing as an organisation, with its signatories approving money leaving, waits for that answer.

## Who applies, and who signs

- **The applicant** is an approved personal client, active, whose own identity check is approved. They choose company or group, give the name (letters, digits, spaces and `& ' . , ( ) -` only, with nothing that reads as a web or email address or a phone number), the registration number (needed for a company, from its certificate; a group may have none), their own role (such as Director or Chairperson), and how many signatories must approve money leaving once the organisation can invest.
- **Signatories** are other approved personal clients. The applicant finds them by Finvest account number or mobile number, as a client finds someone to send an investment to, and the look-ups count against the same limit (20 in 15 minutes). Only a personal account (prefix `10`) can sign. Others see a signatory as their first name and surname initial ("Brian K."), never the full name.
- Each person **joins or declines for themselves**. They are told in the app (`organisation_invited`, sent by the worker because a client's own transaction writes only that client's alerts). The message names who asked, never the organisation: the applicant wrote the name and no member of staff has seen it, and the message goes out in Finvest's voice to someone the applicant found by number. The app shows the name. Someone who declines or is removed no longer sees the application, and the applicant may ask them again.
- At most 10 signatories. A company needs at least one (a sole director) and one approval; a group needs at least two people and two approvals, so no one person holds a group's money alone.

## Documents

| Kind | Documents |
| --- | --- |
| Company | `certificate_of_incorporation`, `board_resolution` (the resolution naming who signs) |
| Group | `constitution`, `group_resolution` (the minutes naming who signs) |

PDF, JPEG or PNG, at most 10 MiB, sent as `multipart/form-data`. The first bytes must match the type claimed, and a refused file never reaches the bucket. A new file replaces the one of the same kind, and the replaced file is deleted once the change commits. Files are under `organisations/<organisation id>/` in the storage bucket. Staff open them through five-minute links, and each viewing is on the audit trail.

## Handing in, and the two-person check

1. **The applicant hands it in** (`POST /client/organisations/{id}/submission`). It is refused, with each reason as a field problem under `problems`, until nobody is still invited, enough people joined, the approvals fit, both documents are in, and a company has its registration number.
2. **A member of staff checks it** with a note: the documents, the registration number against URSB, and that the people named in the resolution are the signatories. Every signatory must still be an approved, active client.
3. **A different member of staff approves it**, with a note and a fresh passkey or app code. The organisation gets its own account number, prefix `30` for a company and `40` for a group, and every signatory is told (`organisation_approved`).

Before it is approved, either member of staff may **send it back** to the applicant as a draft with a note saying what to change (`organisation_returned`), which also undoes the check, or **reject** it for good (`organisation_rejected`). The applicant may apply again after a rejection. All of it needs `organisations.review`, held by admin, operations and compliance. A checked application is in the approvals inbox as `organisation`.

## What the database enforces

Migration `0242_organisations`:

- Only the applicant creates an application, as themselves and as a draft, and only they change it while it is a draft. Who applied, and the kind, never change.
- A draft is handed in only with nobody still invited, at least as many joined as approvals needed, and both documents of its kind.
- Only active staff check, approve, send back or reject. The approver is never the checker. What the client handed in never changes while staff hold it, and an approved or rejected application never changes at all.
- An approved application has an account number with its kind's prefix, unique across organisations. One open application per registration number.
- Signatories change only on a draft. The applicant joins first and stays; the applicant invites, removes and asks again; each person only joins or declines for themselves.
- Documents are added only by the applicant, to a draft, and only of the kinds its kind needs.
- Nothing is deleted. Row policies let a client read only the organisations they applied for or are invited to or joined, through `finvest_organisation_member`, a SECURITY DEFINER function so the three tables' policies do not read each other.

## Routes

| Route | Who | What |
| --- | --- | --- |
| `GET /client/organisations` | Client | Every one they applied for or are asked to sign for |
| `POST /client/organisations` | Client | Apply, as a draft |
| `GET /client/organisations/{id}` | Client | One, with its signatories, documents and `problems` |
| `PATCH /client/organisations/{id}` | Applicant | Change the name, registration number or approvals needed |
| `POST /client/organisations/{id}/signatories` | Applicant | Ask someone, by `to` (account or mobile number) and `role` |
| `DELETE /client/organisations/{id}/signatories/{client_id}` | Applicant | Take someone off |
| `POST /client/organisations/{id}/answer` | Signatory | `{"join": true}` or `false` |
| `PUT /client/organisations/{id}/documents/{kind}` | Applicant | Send a document |
| `POST /client/organisations/{id}/submission` | Applicant | Hand it in |
| `GET /staff/organisations` | Staff | Every application handed in and not decided, oldest first |
| `GET /staff/organisations/{id}` | Staff | One, with each signatory's verified name |
| `POST /staff/organisations/{id}/document-links` | Staff | Five-minute links to the documents |
| `POST /staff/organisations/{id}/check` | Staff | Check it, with a note |
| `POST /staff/organisations/{id}/approval` | A second member of staff | Approve it, with a note and a fresh second step |
| `POST /staff/organisations/{id}/return` | Staff | Send it back to the applicant, with a note |
| `POST /staff/organisations/{id}/rejection` | Staff | Reject it, with a note and a fresh second step |
