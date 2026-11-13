# Console admin and oversight

How the people who run Finvest are added and changed, and the oversight screens of the back office: Team and permissions, the approvals waiting for a second person, the audit trail, email suppressions and client accounts. The screens are on the [design canvas](design.md) under Team and permissions, and Reports and audit.

The standing rule is that a staff operation has a maker and a checker: one person proposes, a different person approves. Identity checks are the only exception ([decisions.md](decisions.md)).

## The team

Staff and their roles live in the identity module (`backend/app/modules/identity`). A change to the team is a row in `identity_staff_changes` (migration `0150_staff_changes`), proposed by one admin and approved by another. Nothing happens to the person until the approval.

| Kind | What approving it does |
| --- | --- |
| `invite` | Adds the person with the role, and emails them a one-time set-up link (`/enrol#…`) that works for 72 hours. They choose a password and add a passkey or an authenticator app. |
| `change_role` | Gives them the new role. The next request they make carries its permissions. |
| `deactivate` | Shuts them out at once: every session they hold ends and any set-up link stops working. Nothing about them is deleted. |
| `reactivate` | Lets a deactivated person back in, as they were: active if they had finished setting up, invited if not. |
| `reset_sign_in` | Emails a new set-up link, for someone who lost their passkey or phone. An earlier link stops working. Their current password and second step keep working until the new link is used. |

| Route | Who | Use |
| --- | --- | --- |
| `GET /staff/team` | `team.view` | Everyone on the team, which second steps each has set up, when each last signed in, what each role may do, and the changes waiting for a second admin. |
| `POST /staff/team/changes` | `team.change` | Propose a change, with a reason. |
| `POST /staff/team/changes/{id}/decision` | `team.change` | `approved` or `rejected` by a different admin, or `withdrawn` by the one who proposed it. A reason is required. |

`team.change` is held by admins only.

Rules, and where they are enforced:

- **Two admins.** The approver is never the proposer, and only the proposer withdraws. The database refuses anything else (`ck_identity_staff_changes_two_people`).
- **Nobody acts on their own account.** Nobody proposes, approves or rejects a change about themselves (`ck_identity_staff_changes_not_own`).
- **Only an approval changes an account.** A trigger on `identity_staff` refuses a new member of staff added by staff, a new role, or a move into or out of `disabled`, unless a change approved in the same transaction says so. An approval from an earlier transaction is not enough, so one cannot be replayed. The first administrator is still added from the command line (`python -m app.modules.identity.bootstrap`), with no acting member of staff.
- **One open change per person,** and one open invite per email address. An address already on the team cannot be invited again.
- **There is always an active admin.** A change that would deactivate or demote the last active admin is refused when it is proposed and again when it is approved. Approvals lock the active admins, so two approvals cannot each remove one of the last two.
- **On the record.** The proposal and the decision are audit events (`identity.team.<kind>_proposed`, `identity.team.change_<decision>`) with their reasons, and a set-up link issued is `identity.staff.enrolment_link_issued`, recorded as the admin who approved it.
