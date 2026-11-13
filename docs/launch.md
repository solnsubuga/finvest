# Launch: parallel run and switch-over

How Finvest 2.0 replaces Finvest 1.0. The migration itself (the import sheet, the trial, the finance check and the claim) is in [migration.md](migration.md); this page is the order of events around it, the conditions for each step, and who decides. Every step names one owner, and a step that changes production waits for Solo's go-ahead.

## 1. Before the parallel run

Staging is set up but switched off to save cost (Solo, 2026-11-13), so production is the only running environment. It is released from the release branch. Until the switch the client app host (`app.finvestug.com`) must be closed to the public with Cloudflare Access letting in staff only, as the back office already is. The infrastructure thread added that Access rule in PR #104, with the same staff policy as the back office. The API host (`api.finvestug.com`) stays public throughout, because ioTec's payment callbacks, Resend's delivery webhook and the phone apps call it directly and cannot pass Access. A public API also means anyone could create a client account through `/client/signup` before the switch, so sign-up must be closed until switch day. That switch is **not built yet** and is a condition below. The parallel run starts only when all of these are true. Each line is ticked with a link to the pull request, drill record or document that proves it.

| Check | Owner | Done |
| --- | --- | --- |
| Phase 0 to 2 features on the [roadmap](roadmap.md) are released to production | Engineering | |
| Cloudflare Access on the client app host, as on the back office, until switch day; the API host stays public (infra PR #104) | Infrastructure | |
| Client sign-up closed by a setting until switch day, answering as if it were off for maintenance | Engineering (sign-in) | |
| Every **Open** finding in [security.md](security.md) is closed or accepted in writing | Engineering, Solo | |
| The external penetration test has been run on staging, switched on for the test weeks only ([security.md](security.md)), and every high or critical finding is fixed and retested | Solo (firm), engineering | |
| A restore drill of the production database passed ([backup-and-restore.md](backup-and-restore.md)) | Engineering | |
| The alerts in [observability.md](observability.md) are on in production, and `ALERT_EMAILS` holds at least two people | Infrastructure | |
| Payment and payout providers are live in production with real credentials, tested with a small real payment and payout | Solo, engineering | |
| The regulator has been told of the change of system, if it must be | Solo | |
| A real Finvest 1.0 export has been through the trial import with no errors | Operations | |

## 2. The parallel run

For about two weeks, Finvest 1.0 stays the system of record and clients keep using it. Finvest 2.0 runs in production, still behind Cloudflare Access, and takes no client sign-ins, payments or payouts. Trials save nothing to the ledger, so the daily trials leave production exactly as clean as before; only the final import on switch day opens investments.

Every business day:

1. Operations exports Finvest 1.0 as of the end of the previous day and runs a trial import with that **as of** date.
2. Finance enters 1.0's own totals for that date, and a second person confirms them.
3. The finance check compares the totals and every investment's interest. Each difference is fixed in the export or signed off by two people with a reason. A sign-off counts only while its exact figures stand, so interest differences, which grow each day, are signed again each day.
4. The day is **clean** when the trial has no error, every difference is signed off, and the totals were confirmed by a second person.

The migration console's trial history shows each day's trial, its as-of date, error and warning counts, differences, unsigned differences, blockers and the two files' fingerprints (the migration thread's `trial_history`).

**The run passes** (proposed, see Open in [decisions.md](decisions.md)) after 5 clean business days in a row, the last of them no more than one business day before the switch. A day that is not clean resets the count. Anything that differs for a reason nobody can explain stops the run until it is explained.

## 3. Go or no go

The day before the switch, Solo, operations and finance meet and go through this list. Any "no" moves the switch.

- [ ] The parallel run passed, and the last clean trial is the file that will be imported.
- [ ] Every item in section 1 is still true, and nothing has been deployed to production since the last restore drill without its own tests passing.
- [ ] Interest for the switch day is understood: the switch happens after the day's 1.0 interest run and before the next.
- [ ] The invitation SMS and email texts are approved.
- [ ] Support has the script for "I got a link", "my number changed" and "my balance looks wrong", and knows how to send a fresh link.
- [ ] Someone is on call with production access for 48 hours after the switch, and Solo can be reached.
- [ ] The rollback decision maker is named (Solo by default).

## 4. Switch day

| Time | Step | Owner |
| --- | --- | --- |
| T-60 min | Announce maintenance on Finvest 1.0 and the website | Operations |
| T-0 | **Freeze Finvest 1.0.** Turn off its writes: no new orders, deposits, redemptions or sign ups. It stays readable. | Engineering |
| T+5 | Export Finvest 1.0 for the final time | Operations |
| T+15 | Run the final trial. It must be clean, and its files must have the same fingerprints as the last cleared trial: the final import refuses any other file. If anything changed since that trial (a payment came in during the day), re-run the finance check for it and get the sign-offs first. | Operations, finance |
| T+30 | Take a manual database backup point: note the time for a point in time restore | Engineering |
| T+35 | **Run the final import.** Each investment opens in the ledger with its original start date, rate and interest to date. Pending orders take today's rate and a fresh 72 hour payment window. | Operations |
| T+60 | Check the ledger against the finance check's totals, and run `backend/db/restore-checks.sql` (read only) to confirm the ledger holds together | Finance, engineering |
| T+70 | Once the ledger check passes, start lifting Cloudflare Access from the client app host: set the repository variable `CLIENT_APP_PUBLIC=true`, then run Provision for production, plan first and then apply after approval. Allow a few minutes for the plan, the approval and the apply. The console keeps Access. | Solo (approval), infrastructure |
| T+80 | Once the apply has finished, open sign-up and turn on payments and payouts. The phone apps were never behind Access: their store release is held until this step. | Engineering, infrastructure |
| T+85 | **Send the invitations.** Every client gets an SMS and an email with a personal link valid for 72 hours, with reminders weekly up to 8. Clients with no phone are listed for staff to call. | System, operations |
| T+90 | Watch: sign ups, claim failures, `/health/operations`, and the first real payments | Everyone |

Details of the import, the claim and the invitations are in [migration.md](migration.md).

## 5. Rollback

Until the first client claims an account or the first payment is taken in Finvest 2.0, rollback is simple: unfreeze Finvest 1.0, close the client app again, and stop invitations. The 2.0 database is left as it is for the investigation; nothing in it has reached a client.

After that point there is no going back to 1.0, because money has moved in 2.0. Problems are fixed forward: a wrong opening balance is corrected with a reversing entry and a two-person correction ([ledger.md](ledger.md)), never by editing a row.

## 6. After the switch

| When | What | Owner |
| --- | --- | --- |
| Daily for 2 weeks | Claimed and unclaimed counts, problems reported at claim, payments and payouts that needed a person | Operations |
| Daily | Reconciliation of the trust account and provider wallets ([reconciliation.md](reconciliation.md)) | Finance |
| After 1 week | First restore drill on production | Engineering |
| After 4 weeks | Decide when Finvest 1.0 is archived. It stays read only until then, and its data is kept for as long as the law requires. | Solo |

## What only Solo can provide

- The penetration testing firm, the test dates and the signed authorisation letter. Staging is switched on for those weeks only.
- Production credentials for the payment and payout providers.
- Whether, when and how the regulator must be told of the change of system.
- The switch date, and the go or no go.
- Approval of the invitation texts and of the recovery targets in [backup-and-restore.md](backup-and-restore.md).
- How long Finvest 1.0's data must be kept after it is archived.
