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
| `POST /staff/team/members/{id}/access-sync` | `team.change` | Bring Cloudflare Access in line with the team again for one person (see below). |

`team.change` is held by admins only.

Other staff routes name who proposed or decided something by staff ID, because their modules do not read identity tables. `GET /staff/people?ids=…` (up to 100) gives the names for those IDs, and with no IDs it lists every active member of staff, for a screen that picks one. Every role holds `overview.view`, which is all it needs, and it returns only IDs and names.

Rules, and where they are enforced:

- **Two admins.** The approver is never the proposer, and only the proposer withdraws. The database refuses anything else (`ck_identity_staff_changes_two_people`), and its guard accepts a proposal or a decision only from an active admin.
- **Nobody acts on their own account.** Nobody proposes, approves or rejects a change about themselves (`ck_identity_staff_changes_not_own`).
- **Only an approval changes an account.** A trigger on `identity_staff` refuses a new member of staff added by staff, a new role, or a move into or out of `disabled`, unless a change approved in the same transaction says so. An approval from an earlier transaction is not enough, so one cannot be replayed. The first administrator is still added from the command line (`python -m app.modules.identity.bootstrap`), with no acting member of staff.
- **One open change per person,** and one open invite per email address. An address already on the team cannot be invited again.
- **There is always an active admin.** A change that would deactivate or demote the last active admin is refused when it is proposed and again when it is approved. Approvals lock the active admins, so two approvals cannot each remove one of the last two.
- **On the record.** The proposal and the decision are audit events (`identity.team.<kind>_proposed`, `identity.team.change_<decision>`) with their reasons, and a set-up link issued is `identity.staff.enrolment_link_issued`, recorded as the admin who approved it.

### Cloudflare Access

The console (admin.finvestug.com), and the client app before launch, sit behind Cloudflare Access. Its policy lets in two sets of addresses: the break-glass list `ADMIN_ACCESS_EMAILS`, which Terraform owns and the console never touches, and a Zero Trust email list that the team keeps (infra PR #539, `cloudflare_zero_trust_list.staff_emails`). Inviting someone in the console is what puts them on that list, so nobody edits Cloudflare by hand.

- **Who belongs on it.** Everyone invited or active. Invited staff need it to open their set-up link. Deactivated staff do not.
- **When it changes.** Approving an `invite`, `deactivate`, `reactivate` or `reset_sign_in` marks the person `waiting` in `identity_staff_access` (migration `0390_staff_access_sync`) and queues `identity.staff_access.sync` in the same transaction. A role change leaves the list alone. A daily check at 05:10 Kampala time (`identity.staff_access.check`) queues the same job for everyone, which covers the first admin added from the command line and any hand edit in Cloudflare.
- **How the worker does it.** It reads the person's address and status when the job runs, asks Cloudflare whether the address is on the list (`GET /accounts/{account}/gateway/lists/{list}/items`), adds or removes it only if it is not where it should be (`PATCH …/gateway/lists/{list}` with `append` or `remove`), and asks again. It records the result only if the team still says the same thing, so a deactivation approved while an invite's add is in flight still ends off the list. An address already there, or already gone, counts as done.
- **Retries.** Every attempt asks Cloudflare before it writes, so a timeout is simply tried again (up to eight attempts over about twenty minutes). That is why the handler is not registered as an external effect. A refused token or a missing list fails at once.
- **What the team view shows.** Each person carries `access`: `in_access` (can reach the console), `removed`, `waiting` (with the reason it is still trying, if any), `failed`, or `not_set_up`. An admin can ask for a failed or never-checked one again with `POST /staff/team/members/{id}/access-sync` (`team.change`, audited as `identity.team.access_sync_requested`). One admin may ask: it only brings Cloudflare in line with changes two admins already approved.
- **Settings, worker only.** `FINVEST_CLOUDFLARE_ACCOUNT_ID` and `FINVEST_CLOUDFLARE_STAFF_LIST_ID` (written by Terraform) and the secret `FINVEST_CLOUDFLARE_ACCESS_TOKEN` (set by the owner; scope Account > Zero Trust > Edit). Without all three the worker sends nothing and the team view says Access sync is off.
- **Privacy.** Job payloads carry the staff ID only, and logs carry staff IDs and error codes, never the address.

### How many admins the scheme needs

Every change needs two admins, and nobody decides a change about themselves. So:

- **With one admin,** nothing can be approved in the console at all.
- **With two admins,** they can invite others and change other people's roles, but neither of them can be deactivated, demoted or sent a new set-up link from the console: the person concerned cannot decide it, and the proposer cannot approve it.
- **With three or more,** every change can be made in the console. This is the working minimum. Plan for three admins on day one (see [launch.md](launch.md)).

Below three, the command line is the way out. `python -m app.modules.identity.bootstrap` adds a person or issues a new set-up link with one operator and no second approval. It is the break-glass path: it needs shell access to production, it is not in the console, and every use should be written up in the incident log.
