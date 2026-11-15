# Full app QA, 15 November 2026

Tested on `main` at `f5b6605` (the report branch is based on `181df5e`), on a local stack: PostgreSQL 16, the API, worker and scheduler, the sandbox payments provider, stub identity and screening providers, and local builds of the client web app, the console and the website. Every account and every record used was created for this run in a database called `finvest_qa`, and that database is dropped when the testing ends. Nothing was signed up, paid or changed in production.

Design source of truth: the [Finvest 2.0 canvas](https://claude.ai/artifact/BkwR1RQ3VFph43JGX9rbuV) (126 boards, each rendered to an image and compared with the build), plus screens the owner approved later in pull requests.

## Summary

Three passes were made. The first covered every journey, screen and control and gave F-001 to F-029. The second went back over limits, staff actions on a client, complaints, support chat, group and company accounts, product forms and ID check edge cases, and gave F-030 to F-033. The third covered statements, goals, automatic investing, rate changes, team changes and announcements with odd but harmless input and found nothing outside F-031.

| Severity | Count | Findings |
| --- | --- | --- |
| Critical | 0 | |
| High | 3 | F-001, F-002, F-003 |
| Medium | 9 | F-004 to F-011, F-030 |
| Low | 21 | F-012 to F-029, F-031 to F-033 |

What passed:

- **Suites on main.** Backend unit and static checks 4,513 passed; integration 2,109 passed against real PostgreSQL; client journeys 10 passed; `pip-audit` clean. Console 968, client 858, website 54 and tokens 4 tests passed, with typecheck and lint clean. The browser suite passed 68 of 68.
- **Screens.** 70 client screens on phone and wide, 23 console addresses for each of the five roles (and for Admin on a phone), and 14 website pages on phone and wide all loaded against the real API with no script error and no sideways scroll. The only failed requests were the two in F-015 and F-021.
- **Money rules checked by hand.** Totals on Home, Portfolio and the statement recompute to the shilling; the same idempotency key creates one investment and one payment; a reused key with a different body is refused; a double tap on Confirm sends one request.
- **Two-person rule.** The same person was refused, and a different person accepted, on payout account approval, rate changes, the trust bank account, complaint outcomes, unfreezing a client, PIN reset, a client's own limits, sending a statement, role changes and announcements. Approvals ask for a fresh second step.
- **Access.** 2,547 calls across all 493 routes were refused exactly as declared (details in section 6).


## 1. Coverage matrix

Every cell is Pass, Fail (with the finding) or N/A (the role cannot reach that journey).

### Role by journey

| Journey | Visitor | Client, new | Client, verified | Admin | Operations | Finance | Compliance | Support |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Website: all 8 pages, legal pages, 404 | Fail (F-003) | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| Website: check a statement code | Pass | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| Sign up: number, SMS code, password, terms | Pass | Pass | N/A | N/A | N/A | N/A | N/A | N/A |
| Log in: password then authenticator code | N/A | Pass | Pass | N/A | N/A | N/A | N/A | N/A |
| Log in: password then SMS code | N/A | Pass | Pass | N/A | N/A | N/A | N/A | N/A |
| Forgot password | Pass | Pass | Pass | N/A | N/A | N/A | N/A | N/A |
| Signed-out visitor opens a signed-in address | Pass | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| Unknown address | Fail (F-006) | Fail (F-006) | Fail (F-006) | Pass | Pass | Pass | Pass | Pass |
| ID check: document, photos, details, selfie, hand in | N/A | Pass | N/A | N/A | N/A | N/A | N/A | N/A |
| ID check: asked for a new photo, retake | N/A | Fail (F-002) | N/A | N/A | N/A | N/A | N/A | N/A |
| Home: first time, empty, with investments | N/A | Pass | Pass | N/A | N/A | N/A | N/A | N/A |
| Invest: product, amount, review, confirm | N/A | Pass | Pass | N/A | N/A | N/A | N/A | N/A |
| Pay by mobile money: prompt, confirmed, receipt | N/A | N/A | Pass | N/A | N/A | N/A | N/A | N/A |
| Pay by mobile money: failed | N/A | N/A | Pass | N/A | N/A | N/A | N/A | N/A |
| Pay by bank transfer: details, receipt upload, waiting | N/A | N/A | Fail (F-004) | N/A | N/A | N/A | N/A | N/A |
| Investment detail and maturity choice | N/A | N/A | Pass | N/A | N/A | N/A | N/A | N/A |
| Send an investment to another client | N/A | N/A | Fail (F-001) | N/A | N/A | N/A | N/A | N/A |
| Withdraw early | N/A | N/A | Fail (F-009) | N/A | N/A | N/A | N/A | N/A |
| Goals: new, plan, first saving, detail, edit | N/A | N/A | Fail (F-020) | N/A | N/A | N/A | N/A | N/A |
| Automatic investing plan | N/A | N/A | Pass | N/A | N/A | N/A | N/A | N/A |
| Portfolio, Activity, Insights | N/A | Pass | Pass | N/A | N/A | N/A | N/A | N/A |
| Documents: statement PDF and Excel, receipts | N/A | N/A | Fail (F-004) | N/A | N/A | N/A | N/A | N/A |
| Payout accounts: add, code, proof, approval, 24 hour wait | N/A | N/A | Pass | N/A | N/A | N/A | N/A | N/A |
| Profile: next of kin, investing questions, language | N/A | Pass | Pass | N/A | N/A | N/A | N/A | N/A |
| Security: devices, passkey screens, authenticator, password, phone, email | N/A | Fail (F-017) | Fail (F-017) | N/A | N/A | N/A | N/A | N/A |
| Close account, freeze, data export | N/A | N/A | Fail (F-008) | N/A | N/A | N/A | N/A | N/A |
| Notifications and settings | N/A | Pass | Pass | N/A | N/A | N/A | N/A | N/A |
| Help: chat message, complaint | N/A | Pass | Pass | N/A | N/A | N/A | N/A | N/A |
| Offline and reconnect | N/A | Fail (F-005) | Fail (F-005) | N/A | N/A | N/A | N/A | N/A |
| Staff sign-in: password, authenticator, wrong password, enrol link | N/A | N/A | N/A | Pass | Pass | Pass | Pass | Pass |
| Console: every screen opens for the role, others refused | N/A | N/A | N/A | Pass | Pass | Pass | Pass | Pass |
| Console: Overview | N/A | N/A | N/A | Pass | Pass | Pass | Pass | Pass |
| Console: Identity checks: ask for photos, approve with second step | N/A | N/A | N/A | Pass | Pass | N/A | Pass | N/A |
| Console: Review queue and payout account, two people | N/A | N/A | N/A | Pass | Pass | Pass | Pass | N/A |
| Console: Clients: find a client | N/A | N/A | N/A | Fail (F-007) | Fail (F-007) | Fail (F-007) | Fail (F-007) | Fail (F-007) |
| Console: Money movement and Reconciliation screens | N/A | N/A | N/A | Fail (F-010) | Fail (F-010) | Fail (F-010) | N/A | N/A |
| Console: Rates: propose, approve by another person | N/A | N/A | N/A | Pass | N/A | Pass | N/A | N/A |
| Console: Settings: trust bank account, two admins | N/A | N/A | N/A | Pass | Pass | Pass | N/A | N/A |
| Console: Support inbox and Complaints | N/A | N/A | N/A | Pass | Pass | N/A | Pass | Pass |
| Console: Investments, Maturities, Goals, Automatic investing | N/A | N/A | N/A | Pass | Pass | Pass | N/A | N/A |
| Console: Dealing desk, Finance, Products | N/A | N/A | N/A | Pass | N/A | Pass | N/A | N/A |
| Console: Compliance | N/A | N/A | N/A | Pass | N/A | N/A | Pass | N/A |
| Console: Team, Reports and audit, Migration | N/A | N/A | N/A | Pass | Pass | Pass | Pass | N/A |
| Console: Your devices, sign out | N/A | N/A | N/A | Pass | Pass | Pass | Pass | Pass |

### Journey by state

| Journey | Default | Empty | Error or refused | Odd input | Interrupted (refresh, back, double tap, two sessions) | Phone | Wide |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Sign up | Pass | Pass | Pass | Pass | Pass | Pass | Pass |
| Log in and reset | Pass | N/A | Pass | Pass | Fail (F-020) | Pass | Pass |
| ID check | Pass | Pass | Pass | Pass | Fail (F-002) | Pass | Pass |
| Home | Pass | Pass | Fail (F-005) | N/A | Pass | Pass | Pass |
| Invest and pay by mobile money | Pass | Pass | Pass | Pass | Pass | Pass | Pass |
| Pay by bank transfer | Fail (F-004) | Pass | Pass | Fail (F-027) | Pass | Pass | Pass |
| Investment detail, maturity choice | Pass | N/A | Pass | Pass | Pass | Pass | Pass |
| Send | Fail (F-001) | N/A | Pass | Pass | N/A | Pass | Pass |
| Withdraw and payout accounts | Fail (F-009) | Pass | Pass | Fail (F-027) | Pass | Pass | Pass |
| Goals and automatic investing | Pass | Pass | Pass | Pass | Fail (F-020) | Pass | Pass |
| Portfolio, Activity, Insights, Documents | Fail (F-004) | Pass | Pass | Pass | Pass | Pass | Pass |
| Profile, Security, Close account | Fail (F-008) | Pass | Pass | Pass | Pass | Pass | Pass |
| Help and complaints | Pass | Pass | Pass | Pass | Pass | Pass | Pass |
| Console sign-in | Pass | N/A | Pass | Pass | Pass | Pass | Pass |
| Console daily work screens | Pass | Pass | Pass | Pass | Pass | Fail (F-021) | Pass |
| Console clients and money screens | Fail (F-007, F-010) | Pass | Pass | Pass | Pass | Pass | Pass |
| Console catalogue and control screens | Pass | Pass | Pass | Pass | Pass | Pass | Fail (F-021) |
| Website | Fail (F-003) | N/A | Pass | Pass | N/A | Pass | Pass |

### Console screens by role

Opens means the screen loaded against the real API with no error. Refused means the console showed "You cannot open this screen" and the API answered 403.

| Screen | Admin | Operations | Finance | Compliance | Support |
| --- | --- | --- | --- | --- | --- |
| Overview | Opens, Pass | Opens, Pass | Opens, Pass | Opens, Pass | Opens, Pass |
| Review queue | Opens, Pass | Opens, Pass | Opens, Pass | Opens, Pass | Refused, Pass |
| Identity checks | Opens, Pass | Opens, Pass | Refused, Pass | Opens, Pass | Refused, Pass |
| Maturities | Opens, Pass | Opens, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Support | Opens, Pass | Opens, Pass | Refused, Pass | Refused, Pass | Opens, Pass |
| Complaints | Opens, Pass | Opens, Pass | Refused, Pass | Opens, Pass | Opens, Pass |
| Clients | Opens, Pass | Opens, Pass | Opens, Pass | Opens, Pass | Opens, Pass |
| Money movement | Opens, Pass | Opens, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Reconciliation | Opens, Pass | Opens, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Investments | Opens, Pass | Opens, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Automatic investing | Opens, Pass | Opens, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Goals | Opens, Pass | Opens, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Dealing desk | Opens, Pass | Refused, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Finance | Opens, Pass | Refused, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Products | Opens, Pass | Refused, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Rates | Opens, Pass | Refused, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Compliance | Opens, Pass | Refused, Pass | Refused, Pass | Opens, Pass | Refused, Pass |
| Team | Opens, Pass | Refused, Pass | Refused, Pass | Refused, Pass | Refused, Pass |
| Reports | Opens, Pass | Opens, Pass | Opens, Pass | Opens, Pass | Refused, Pass |
| Settings | Opens, Pass | Opens, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Migration | Opens, Pass | Opens, Pass | Opens, Pass | Refused, Pass | Refused, Pass |
| Devices | Opens, Pass | Opens, Pass | Opens, Pass | Opens, Pass | Opens, Pass |

## 2. Design match

The design reference is the board of that name on the [Finvest 2.0 canvas](https://claude.ai/artifact/BkwR1RQ3VFph43JGX9rbuV). Build screenshots are in `docs/qa/screenshots/`.

| Design board | State | Build screenshot | Result | What differs | Findings |
| --- | --- | --- | --- | --- | --- |
| Logo | Brand mark | [10-index](screenshots/site-wide/10-index.jpg) | Match | Rising line with lime dot, lower case wordmark. |  |
| Welcome | Default | [01-welcome](screenshots/client-phone/01-welcome.jpg) | Match | Adds an EXAMPLE tag and a Paid from MoMo chip. |  |
| SignupStart | Default | [02-signup-create-account](screenshots/client-phone/02-signup-create-account.jpg) | Match |  |  |
| SignupStart | Empty and terms not ticked | [04-signup-terms-not-ticked](screenshots/client-phone/04-signup-terms-not-ticked.jpg) | Differs | Terms error is colour only. | F-014 |
| Signup | Enter code, wrong code | [06-signup-wrong-code](screenshots/client-phone/06-signup-wrong-code.jpg) | Match |  |  |
| CreatePassword | Default and refused password | [08-signup-common-password](screenshots/client-phone/08-signup-common-password.jpg) | Differs | Meter says Good while refused. | F-013 |
| CreatePin | Phone app PIN | [97-setup-pin](screenshots/client-phone/97-setup-pin.jpg) | Match | PIN is for the phone apps only; the web shows why. |  |
| PasskeySetup | Default | [98-setup-passkey](screenshots/client-phone/98-setup-passkey.jpg) | Match |  |  |
| AccountType | Personal, joint, group, company | [85-account-organisation-new](screenshots/client-phone/85-account-organisation-new.jpg) | Differs | Company and group accounts start from Profile, not at sign-up; joint accounts are later by decision. | F-024 |
| AccountReady | After ID approval | [80-id-check-approved](screenshots/client-phone-ama/80-id-check-approved.jpg) | Match |  |  |
| LoginStart | Default and wrong password | [21-login-wrong-password](screenshots/client-phone/21-login-wrong-password.jpg) | Match | Adds Log in with a passkey and Use email instead. |  |
| LoginVerify | Second step choices | [22-login-second-step](screenshots/client-phone/22-login-second-step.jpg) | Match |  |  |
| Login | Authenticator and SMS code | [24-login-sms-code](screenshots/client-phone-ama/24-login-sms-code.jpg) | Match |  |  |
| ForgotPassword | Start, code, done | [26-reset-code](screenshots/client-phone-flows/26-reset-code.jpg) | Match |  |  |
| ForgotPin | Forgotten PIN | [97-setup-pin](screenshots/client-phone/97-setup-pin.jpg) | Differs | By decision a forgotten PIN means log in again; no separate screen. |  |
| KycChoose | Default | [61-id-check-choose](screenshots/client-phone-ama/61-id-check-choose.jpg) | Match | Adds country and Refugee ID. |  |
| KycFront | Guide and photo check | [63-id-check-front-chosen](screenshots/client-phone-ama/63-id-check-front-chosen.jpg) | Match |  |  |
| KycBack | Guide | [64-id-check-back](screenshots/client-phone-ama/64-id-check-back.jpg) | Match |  |  |
| KycDetails | Default, empty, invalid | [67-id-check-details-invalid](screenshots/client-phone-ama/67-id-check-details-invalid.jpg) | Match | Typed by the client; the canvas shows details read from the ID. |  |
| Kyc | Selfie | [68-id-check-selfie](screenshots/client-phone-ama/68-id-check-selfie.jpg) | Differs | Live check on the provider page, by decision. |  |
| KycStatus | In review | [72-id-check-status-in-review](screenshots/client-phone-ama/72-id-check-status-in-review.jpg) | Match |  |  |
| KycStatus | New photo asked | [77-retake-selfie-shows-old-live-check](screenshots/client-phone-ama/77-retake-selfie-shows-old-live-check.jpg) | Differs | Retake cannot be completed. | F-002 |
| EmptyHome | First time | [60-home-first-time](screenshots/client-phone-ama/60-home-first-time.jpg) | Match |  |  |
| Main | Home with investments | [30-home](screenshots/client-phone/30-home.jpg) | Match |  |  |
| WebHome | Wide Home | [30-home](screenshots/client-wide/30-home.jpg) | Differs | Sidebar grouped and shows Signed in as, not the account number. | F-024 |
| Discover | Invest list | [31-invest](screenshots/client-phone/31-invest.jpg) | Differs | No My portfolio button, banner, filter chips or Start from, Term, Interest paid row. Dollar product hidden by decision. | F-024 |
| WebInvest | Wide Invest | [31-invest](screenshots/client-wide/31-invest.jpg) | Differs | No dollar column; amount is typed, not a slider; Continue, not Continue to payment. | F-024 |
| Product | Default | [39-product-trea-91](screenshots/client-phone/39-product-trea-91.jpg) | Differs | Typed amount not a slider, no facts table, black Review not lime Invest. | F-024, F-026 |
| UsdProduct | Dollar product | [31-invest](screenshots/client-phone/31-invest.jpg) | Missing | Paused by decision until dollars can be paid in and out. |  |
| InvestConfirm | Review | [41-invest-review](screenshots/client-phone/41-invest-review.jpg) | Differs | Three choices at maturity instead of a switch; no Pay with or Starts rows. Approved later in PRs. |  |
| AddMoney | Choose how to pay | [48-pay-choose-method](screenshots/client-phone/48-pay-choose-method.jpg) | Differs | Grey network tiles, no fee row, black button. | F-024, F-026 |
| WebPay | Wide pay | [48-pay-choose-method](screenshots/client-wide/48-pay-choose-method.jpg) | Differs | No Review, Pay, Done stepper; no fee row. | F-024 |
| MomoConfirm | Waiting for approval | [48b-pay-waiting-for-approval](screenshots/client-phone-carl/48b-pay-waiting-for-approval.jpg) | Match |  |  |
| Receipt | Payment receipt | [48c-pay-confirmed](screenshots/client-phone-carl/48c-pay-confirmed.jpg) | Differs | Reference is a raw ID; no network reference or account number. | F-004 |
| PaymentFailed | Failed | [48d-pay-failed](screenshots/client-phone-flows/48d-pay-failed.jpg) | Match |  |  |
| BankTransfer | Bank details | [49-pay-bank-transfer-details](screenshots/client-phone-flows/49-pay-bank-transfer-details.jpg) | Match |  |  |
| BankReceipt | Upload, empty, odd values | [50b-bank-receipt-empty-errors](screenshots/client-phone-flows/50b-bank-receipt-empty-errors.jpg) | Differs | No Take a photo, no reading of the receipt, bank typed not picked. | F-024, F-027 |
| BankStatus | Waiting for match | [50d-bank-receipt-sent](screenshots/client-phone-flows/50d-bank-receipt-sent.jpg) | Differs | Reference is a raw ID. | F-004 |
| Portfolio | Default | [34-portfolio](screenshots/client-phone/34-portfolio.jpg) | Differs | No progress bar or today figure per investment. | F-024 |
| WebPortfolio | Wide | [34-portfolio](screenshots/client-wide/34-portfolio.jpg) | Match | Adds a split bar. | F-019 |
| Investment | Running | [42-investment-active](screenshots/client-phone/42-investment-active.jpg) | Differs | No daily chart, no today figure, no reinvest switch. | F-024 |
| Investment | Awaiting payment | [43-investment-awaiting-payment](screenshots/client-phone/43-investment-awaiting-payment.jpg) | Match |  |  |
| MaturityChoice | Default | [44-investment-maturity-choice](screenshots/client-phone/44-investment-maturity-choice.jpg) | Differs | No Move it to a goal (later by decision); no amounts per choice; no If you do nothing note; black Save. | F-024, F-026 |
| Redeem | Withdraw | [47-investment-withdraw](screenshots/client-phone/47-investment-withdraw.jpg) | Differs | Whole early withdrawal only. | F-009 |
| Transfer | Send | [46c-send-review](screenshots/client-phone-flows/46c-send-review.jpg) | Differs | Two steps with Find; cannot complete. | F-001 |
| Goals | List | [32-goals](screenshots/client-phone/32-goals.jpg) | Differs | Full width New goal button, no auto-saving row, group card reworded. | F-024 |
| GoalNew | Step 1 | [53-goals-new](screenshots/client-phone/53-goals-new.jpg) | Differs | No Save with a group tile on this step. | F-024 |
| GoalPlan | Step 2 | [55-goals-plan](screenshots/client-phone/55-goals-plan.jpg) | Differs | Chips not a slider; plan card not shown until an amount is typed. | F-024 |
| GoalFunding | Step 3 | [54-goals-start](screenshots/client-phone/54-goals-start.jpg) | Differs | First saving form instead of funding switches. | F-020, F-024 |
| GoalDetail | Default | [56-goal-detail](screenshots/client-phone/56-goal-detail.jpg) | Differs | No Edit plan link, Next saving, or Save now, Pause, Withdraw row on this screen. | F-024 |
| GroupSetup | Group goal | [60-goal-group-new](screenshots/client-phone/60-goal-group-new.jpg) | Match |  |  |
| Activity | Default | [33-activity](screenshots/client-phone/33-activity.jpg) | Differs | No search or Get statement on the phone; filters Payments, Interest, Payouts; grouped by month; amounts unsigned. | F-024 |
| WebActivity | Wide | [33-activity](screenshots/client-wide/33-activity.jpg) | Differs | Receipt panel lacks references and Download receipt. | F-004, F-024 |
| Insights | Default | [38-insights](screenshots/client-phone/38-insights.jpg) | Differs | Six months, no Put it to work button. | F-024 |
| Statements | Documents | [36-documents](screenshots/client-phone/36-documents.jpg) | Match | Make statement, not Create statement. |  |
| WebDocuments | Wide | [36-documents](screenshots/client-wide/36-documents.jpg) | Differs | Cards and a dark statement panel instead of a table; no Email it to me; names carry raw IDs. | F-004, F-024 |
| TaxDocs | Tax documents | [36-documents](screenshots/client-phone/36-documents.jpg) | Match | Inside Documents. |  |
| Profile | Default | [35-profile](screenshots/client-phone/35-profile.jpg) | Differs | No avatar, account number or Verified badge at the top; no Limits row; different grouping. | F-024 |
| LinkedAccounts | Payout accounts | [65-payout-accounts](screenshots/client-phone/65-payout-accounts.jpg) | Differs | Different title and grouping, no Default badge, limits Not set. | F-018, F-024 |
| AddBank | Add payout account | [66-payout-account-add](screenshots/client-phone/66-payout-account-add.jpg) | Differs | Name is typed (canvas locks it to the ID name); no shillings or dollars switch. | F-027 |
| AddBankConfirm | Code and proof | [67-payout-account-detail](screenshots/client-phone/67-payout-account-detail.jpg) | Match |  |  |
| Security | Default | [69-security](screenshots/client-phone/69-security.jpg) | Differs | No Asked again for section. | F-024 |
| Devices | Default | [70-security-devices](screenshots/client-phone/70-security-devices.jpg) | Differs | No Waiting for you approval of a new device, no location; Unknown device. | F-017, F-024 |
| NextOfKin | Default | [79-account-next-of-kin](screenshots/client-phone/79-account-next-of-kin.jpg) | Differs | No shares, by decision. |  |
| RiskProfile | Investing questions | [80-account-investing-questions](screenshots/client-phone/80-account-investing-questions.jpg) | Match |  |  |
| Language | Default | [81-account-language](screenshots/client-phone/81-account-language.jpg) | Differs | Luganda and Kiswahili Coming soon; no USSD note. |  |
| Notifications | Inbox and settings | [86-notifications](screenshots/client-phone/86-notifications.jpg) | Differs | No amounts in notices, by rule. |  |
| Support | Help | [37-help](screenshots/client-phone/37-help.jpg) | Differs | No Call us. | F-023 |
| CloseAccount | Blocked by running investments | [83-account-close](screenshots/client-phone/83-account-close.jpg) | Differs | Counts disagree. | F-008 |
| ClaimAccount | Claim, no link | [95-claim-no-token](screenshots/client-phone/95-claim-no-token.jpg) | Match | Only the no-link state could be opened. |  |
| ClaimConfirm | Confirm | [95-claim-no-token](screenshots/client-phone/95-claim-no-token.jpg) | Missing | Not tested: needs an imported client. |  |
| Admin | Review queue | [11-review-queue](screenshots/console-wide-admin/11-review-queue.jpg) | Differs | List rows with Open instead of a side panel. | F-025 |
| AdminNav | Sidebar | [10-overview](screenshots/console-wide-admin/10-overview.jpg) | Differs | Same groups and counts; needs scrolling at 900 high. | F-021 |
| AdminOverview | Default | [10-overview](screenshots/console-wide-admin/10-overview.jpg) | Differs | Adds floats, daily balancing and waiting approvals; no Today or Export buttons. | F-025 |
| AdminKyc | Waiting, asked, approved | [12-identity-checks-waiting](screenshots/console-wide-flows/12-identity-checks-waiting.jpg) | Match | Adds reason, photos to retake and a confirm step. |  |
| AdminMaturities | Empty | [13-maturities](screenshots/console-wide-admin/13-maturities.jpg) | Differs | No summary tiles, table, Export or Coming up while empty. | F-025 |
| AdminSupport | Inbox | [14-support](screenshots/console-wide-admin/14-support.jpg) | Differs | Announcements moved to Settings; About this client lacks Payout pending and Invested. | F-025 |
| AdminComplaints | Register | [15-complaints](screenshots/console-wide-admin/15-complaints.jpg) | Differs | Regulator report lives in Reports; no Export register here. | F-025 |
| AdminClients | Find | [16-clients](screenshots/console-wide-admin/16-clients.jpg) | Differs | No name search or list. | F-007 |
| AdminMoney | Default | [17-money-movement](screenshots/console-wide-admin/17-money-movement.jpg) | Differs | Exceptions only. | F-010 |
| AdminRecon | Default | [18-reconciliation](screenshots/console-wide-admin/18-reconciliation.jpg) | Differs | No client money check panel. | F-010 |
| AdminDeposit | Bank transfer to check | [18-reconciliation](screenshots/console-wide-admin/18-reconciliation.jpg) | Differs | Lives at the top of Reconciliation. | F-025 |
| AdminInvestments | Default | [19-investments](screenshots/console-wide-admin/19-investments.jpg) | Match | All bulk actions present. |  |
| AdminDealing | Default | [22-dealing-desk](screenshots/console-wide-admin/22-dealing-desk.jpg) | Differs | No Record a purchase or Allocation report buttons; 0.3m rounding. | F-021, F-025 |
| AdminFinance | Default | [23-finance](screenshots/console-wide-admin/23-finance.jpg) | Differs | No Prepare the URA file or Tax return file buttons; adds Close the month. | F-025 |
| AdminProducts | Default | [24-products](screenshots/console-wide-admin/24-products.jpg) | Match | Read panel with Edit. |  |
| AdminRates | Default | [25-rates](screenshots/console-wide-admin/25-rates.jpg) | Differs | No Rate history button, Last auction tile or dollar rate row. | F-025 |
| AdminAml | Compliance | [26-compliance](screenshots/console-wide-admin/26-compliance.jpg) | Differs | No New report to FIA or Rules buttons (reports are filed on the FIA system). | F-025 |
| AdminTeam | Default | [27-team](screenshots/console-wide-admin/27-team.jpg) | Differs | No New role; roles are fixed. | F-025 |
| AdminReports | Default | [28-reports](screenshots/console-wide-admin/28-reports.jpg) | Match | Seven reports and a fuller audit trail. |  |
| AdminSettings | Default | [29-settings](screenshots/console-wide-admin/29-settings.jpg) | Differs | No Fees card; sign out after 30 minutes; limits Not set. | F-018, F-021 |
| AdminMigration | Empty | [30-migration](screenshots/console-wide-admin/30-migration.jpg) | Differs | Fewer actions while empty; says Finvest 1.0. | F-021 |
| SiteHome | Wide | [10-index](screenshots/site-wide/10-index.jpg) | Differs | No Regulated strip item, store buttons or regulatory footer line. | F-003, F-023 |
| SiteHomeMobile | Phone | [10-index](screenshots/site-phone/10-index.jpg) | Match | Full rate cards instead of compact rows. |  |
| SiteProducts | Wide | [11-products](screenshots/site-wide/11-products.jpg) | Match | Adds five product kinds and Bank of Uganda auction results. |  |
| SiteHow | Wide | [12-how-it-works](screenshots/site-wide/12-how-it-works.jpg) | Match |  |  |
| SiteSafety | Wide | [13-safety](screenshots/site-wide/13-safety.jpg) | Differs | No Licensed and supervised block. | F-003 |
| SiteAbout | Wide | [14-about](screenshots/site-wide/14-about.jpg) | Differs | No team section, no phone number. | F-023 |
| SiteHelp | Wide | [15-help](screenshots/site-wide/15-help.jpg) | Differs | No Call us card. | F-023 |
| SiteLegal | Terms | [17-legal-terms](screenshots/site-wide/17-legal-terms.jpg) | Differs | Headings only. | F-003 |

### Emails

Built emails are the committed renders in `docs/screenshots/emails/`. Client notices use the designed band, card and footer but carry no amounts, greeting or per-event button, because notices must not carry amounts (docs/rules.md and the decisions log). They are marked Differs, by decision.

| Design board | Build screenshot | Result | What differs |
| --- | --- | --- | --- |
| EmailInvestmentStarted | [notice-investment-started-phone](../screenshots/emails/notice-investment-started-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailPaymentFailed | [notice-payment-failed-phone](../screenshots/emails/notice-payment-failed-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailPayoutProcessing | [notice-payout-processing-phone](../screenshots/emails/notice-payout-processing-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailPayoutSent | [notice-payout-sent-phone](../screenshots/emails/notice-payout-sent-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailPayoutFailed | [notice-payout-failed-phone](../screenshots/emails/notice-payout-failed-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailKycApproved | [notice-kyc-approved-phone](../screenshots/emails/notice-kyc-approved-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailKycRejected | [notice-kyc-rejected-phone](../screenshots/emails/notice-kyc-rejected-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailKycRetake | [notice-kyc-retake-requested-phone](../screenshots/emails/notice-kyc-retake-requested-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailInterest | [notice-interest-earned-phone](../screenshots/emails/notice-interest-earned-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailMaturing | [notice-investment-maturing-phone](../screenshots/emails/notice-investment-maturing-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailMatured | [notice-investment-matured-phone](../screenshots/emails/notice-investment-matured-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailGoalReached | [notice-goal-reached-phone](../screenshots/emails/notice-goal-reached-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailGoalTopUp | [notice-goal-top-up-due-phone](../screenshots/emails/notice-goal-top-up-due-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailPasswordChanged | [notice-password-changed-phone](../screenshots/emails/notice-password-changed-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailSecondStep | [notice-second-step-changed-phone](../screenshots/emails/notice-second-step-changed-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailPayoutAccountApproved | [notice-payout-destination-approved-phone](../screenshots/emails/notice-payout-destination-approved-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailPayoutAccountRefused | [notice-payout-destination-refused-phone](../screenshots/emails/notice-payout-destination-refused-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailComplaintReceived | [notice-complaint-received-phone](../screenshots/emails/notice-complaint-received-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailComplaintClosed | [notice-complaint-closed-phone](../screenshots/emails/notice-complaint-closed-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailSupportReply | [notice-support-reply-phone](../screenshots/emails/notice-support-reply-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailAnnouncement | [notice-announcement-phone](../screenshots/emails/notice-announcement-phone.png) | Differs | No amount, facts table or greeting; one Open Finvest button. By decision. |
| EmailReceipt | [document-receipt-phone](../screenshots/emails/document-receipt-phone.png) | Match |  |
| EmailMonthlyStatement | [document-monthly-statement-phone](../screenshots/emails/document-monthly-statement-phone.png) | Match |  |
| EmailRequestedStatement | [document-requested-statement-phone](../screenshots/emails/document-requested-statement-phone.png) | Match |  |
| EmailSignInCode | [sign-in-code-phone](../screenshots/emails/sign-in-code-phone.png) | Match |  |
| EmailResetCode | [password-reset-code-phone](../screenshots/emails/password-reset-code-phone.png) | Match |  |
| EmailNewSignIn | [new-sign-in-phone](../screenshots/emails/new-sign-in-phone.png) | Match |  |
| EmailStaffInvite | [staff-invite-phone](../screenshots/emails/staff-invite-phone.png) | Match |  |
| EmailClaim | [claim-invitation-phone](../screenshots/emails/claim-invitation-phone.png) | Match |  |

## 3. Findings

| ID | Title | Area | Severity | Affected roles |
| --- | --- | --- | --- | --- |
| F-001 | Sending an investment to another client always fails | Logic | High | Client (verified) |
| F-002 | A client asked for a new selfie cannot take one when the live check is on | Logic | High | Client (ID check in progress), Operations, Compliance |
| F-003 | Legal pages are headings only, and the website names no company, regulator or licence | UI | High | Visitor, Client |
| F-004 | Statement PDF prints the reference over the amount, and clients are shown raw internal IDs as references | UI | Medium | Client (verified) |
| F-005 | On the web, opening a screen for the first time while offline leaves a blank page that never recovers | UI | Medium | Client |
| F-006 | An unknown address shows Expo's default "Unmatched Route" page | UI | Medium | Visitor, Client |
| F-007 | Console Clients can only find a client by exact account number, mobile number or ID | Design match | Medium | Admin, Operations, Finance, Compliance, Support |
| F-008 | Close account, Home and Portfolio disagree on how many investments are running | Logic | Medium | Client (verified) |
| F-009 | Withdraw offers only a whole early withdrawal | Design match | Medium | Client (verified) |
| F-010 | Console has no list of every payment in and out, and Reconciliation has no client money check | Design match | Medium | Admin, Operations, Finance |
| F-011 | The first visit downloads about 1.9 MB of script on the client app and 1.7 MB on the console | Performance | Medium | Visitor, Client, Staff |
| F-012 | The yearly rate is shown rounded up, so quotes and the website's estimate disagree | Logic | Low | Visitor, Client |
| F-013 | Password strength says "Good password" while the leaked-password error is showing | UI | Low | Visitor |
| F-014 | Terms not ticked is shown by a red box only | Accessibility | Low | Visitor |
| F-015 | The app asks for a claim on every start and gets a 404 | UI | Low | Client |
| F-016 | Pay out screen for a running investment says the money is already on its way | UI | Low | Client (verified) |
| F-017 | Devices lists "Unknown device" and "Signed in with sign up" | UI | Low | Client |
| F-018 | Payout limits read "Not set" | UI | Low | Client, Admin, Operations, Finance |
| F-019 | Three automated accessibility findings in the client app | Accessibility | Low | Client |
| F-020 | Opening a later step of a flow directly leaves a dead end | UI | Low | Client |
| F-021 | Console rough edges | UI | Low | Staff |
| F-022 | Finance month close is guarded by a view permission | Security | Low | Admin, Finance |
| F-023 | Website leaves out a few designed items | Design match | Low | Visitor |
| F-024 | Client screens differ from the canvas in detail | Design match | Low | Client |
| F-025 | Console screens differ from the canvas in detail | Design match | Low | Staff |
| F-026 | The main button is lime on the canvas and black in several built flows | Design match | Low | Client |
| F-027 | Forms accept values that are refused later | UI | Low | Client |
| F-028 | A password reset accepts the password already in use | Security | Low | Client |
| F-029 | Tablet width uses the phone layout stretched | UI | Low | Client |
| F-030 | Approving a second account on an ID number already in use fails with the wrong message | Logic | Medium | Operations, Compliance, Admin |
| F-031 | No sensible upper limit on amounts and ages | Logic | Low | Client |
| F-032 | Console client record shows internal IDs and a misleading label | UI | Low | All staff roles |
| F-033 | Small gaps in checks on drafts and proposals | Logic | Low | Client, Admin, Finance |

### F-001 Sending an investment to another client always fails

- **Area:** Logic
- **Severity:** High
- **Affected roles:** Client (verified)
- **Steps:** 1. Log in as a verified client with a running investment. 2. Open the investment, choose Send. 3. Enter another client's account number, Find. 4. Enter an amount, pick a reason, Check and send, then confirm.
- **Expected:** The share is sent (or held for review if large) and both clients are told.
- **Actual:** `POST /client/investments/{id}/send` answers 422 every time (`is_instance_of` on `currency` and `purpose`). The app shows "We could not accept that. Go back, check the details and try again." Nothing can be sent by anyone.
- **Evidence:** [screenshot](screenshots/client-phone-flows/46d-send-fails.jpg). `SendRequest` in `backend/app/modules/investments/transfers_api.py` types `currency` and `purpose` as bare `Currency` and `TransferPurpose` enums under the strict `Contract`, so a JSON string is never accepted. A scan of all 218 request models found this is the only one. The integration tests call the service, never the route.
- **Suggested fix:** Annotate both fields with `Field(strict=False)` as other modules do, and add a route-level test that posts JSON.

### F-002 A client asked for a new selfie cannot take one when the live check is on

- **Area:** Logic
- **Severity:** High
- **Affected roles:** Client (ID check in progress), Operations, Compliance
- **Steps:** 1. Client hands in an ID check whose selfie came from the live check. 2. Staff choose Ask for new photos, Selfie. 3. Client opens ID check and taps Your selfie.
- **Expected:** The client can start a new live check (or use the camera) and send the new selfie.
- **Actual:** The app shows the old result as "Live selfie done" with only "Send my new selfie". It marks the selfie "Taken again", and sending is refused with "Take the photo again first, then send it." No button starts a new check, so the client is stuck until staff reject the whole check.
- **Evidence:** [old result offered](screenshots/client-phone-ama/77-retake-selfie-shows-old-live-check.jpg), [refused](screenshots/client-phone-ama/78-retake-after-send.jpg). `apps/client/src/identity/LiveSelfie.tsx` returns the done screen whenever `latest.status === "passed"`; `backend/app/modules/kyc/liveness.py` `_LATEST` returns the newest attempt whatever the retake request time. The server side rule is right (`retaken` only counts photos newer than the request).
- **Suggested fix:** Treat a passed attempt older than `retake_requested_at` as used: have the availability route hide it, or have the screen offer Start live check when the selfie is in `retakePhotos` and not yet retaken. Add a test for retake after a passed live check.

### F-003 Legal pages are headings only, and the website names no company, regulator or licence

- **Area:** UI
- **Severity:** High
- **Affected roles:** Visitor, Client
- **Steps:** 1. Open finvestug.com/legal/terms (and privacy, risk, fees, complaints, cookies, and the PDFs). 2. Look for who runs Finvest on any page.
- **Expected:** Final terms, privacy notice and other legal wording; the registered company name, regulator and licence number on the site (the canvas shows them in the footer, the trust strip and Safety).
- **Actual:** Each legal page says "Layout only. The headings below show the structure. The wording must come from Finvest's lawyers before launch." Clients accept these terms at sign-up. `registeredName`, `regulator` and `licenceNumber` are empty, so the footer line, the "Regulated" strip item and the "Licensed and supervised" block are left out. `pnpm placeholders --strict` fails with 3 facts missing.
- **Evidence:** [terms](screenshots/site-wide/17-legal-terms.jpg). Known in `docs/gaps.md` line 118 and 135. It needs facts and wording from the owner, not code.
- **Suggested fix:** Owner supplies the company facts and the lawyers' wording; then set `apps/site/src/data/company.ts`, fill the legal pages and PDFs, and rerun the strict placeholder check.

### F-004 Statement PDF prints the reference over the amount, and clients are shown raw internal IDs as references

- **Area:** UI
- **Severity:** Medium
- **Affected roles:** Client (verified)
- **Steps:** 1. Documents, make a statement for a period with payments. 2. Open the PDF, look at What happened. 3. Also look at a payment receipt, the bank transfer status screen and the Documents list.
- **Expected:** Readable columns, and short references a person can quote (the canvas shows FV-O9cK2mXa).
- **Actual:** In the statement the 20 character reference (for example -P6TNMgBJ0qtT-C9Ed1Q) runs into the Money in column and the amount is printed on top of it. Receipts show "Finvest reference -P6TYZZTunCqFtwZMYmp", the bank transfer screen shows "Bank transfer, reference -P6TXqt7btW2IECNl9Hc", documents are named ST--P6T…, RC--P6T…, IN--P6T… with a double dash. The IDs start with a dash and are hard to read out.
- **Evidence:** [statement page](screenshots/evidence/statement-page-1.jpg), [receipt](screenshots/client-phone-carl/48c-pay-confirmed.jpg), [bank transfer status](screenshots/client-phone-flows/50d-bank-receipt-sent.jpg).
- **Suggested fix:** Give payments and documents a short human reference (as the canvas does), or at least narrow the ID and widen the column; never start a reference with a dash.

### F-005 On the web, opening a screen for the first time while offline leaves a blank page that never recovers

- **Area:** UI
- **Severity:** Medium
- **Affected roles:** Client
- **Steps:** 1. Log in on app.finvestug.com and stay on Home. 2. Lose the connection. 3. Tap a tab not opened yet in this visit (for example Invest).
- **Expected:** The "No connection" banner with Try again, as on screens already opened.
- **Actual:** A blank white page with no message. The screen's code is loaded on demand and the load fails (`AsyncRequireError`). It stays blank after the connection returns, until the person reloads the page by hand.
- **Evidence:** [blank](screenshots/client-phone-flows/95c-offline-blank-screen.jpg), compare [banner on a loaded tab](screenshots/client-phone-flows/95b-offline-banner.jpg).
- **Suggested fix:** Catch failed route loads in an error boundary that shows the offline state and retries, or preload the tab screens after sign-in.

### F-006 An unknown address shows Expo's default "Unmatched Route" page

- **Area:** UI
- **Severity:** Medium
- **Affected roles:** Visitor, Client
- **Steps:** Open app.finvestug.com/this-page-does-not-exist.
- **Expected:** A Finvest page that says the screen was not found, with a way back.
- **Actual:** The framework's stock page: "Unmatched Route", the raw URL, a "Sitemap" link that lists every route in the app, and the start-up text "This is taking longer than usual". axe reports nested interactive controls there.
- **Evidence:** [screenshot](screenshots/client-phone/96-not-found.jpg).
- **Suggested fix:** Add `+not-found.tsx` with the designed look, and remove the sitemap route from production builds.

### F-007 Console Clients can only find a client by exact account number, mobile number or ID

- **Area:** Design match
- **Severity:** Medium
- **Affected roles:** Admin, Operations, Finance, Compliance, Support
- **Steps:** Open Clients in the console and try to find a client by name or email.
- **Expected:** The canvas (AdminClients): a search by name, phone, email or account number, a results table with filters, Export list and Add a client.
- **Actual:** One box that takes an exact account number, mobile number or client ID. No name or email search, no list of clients, no filters.
- **Evidence:** [build](screenshots/console-wide-admin/16-clients.jpg) against board AdminClients.
- **Suggested fix:** Add a name and email search with a paged results table (names are stored in plain text, so this needs no new index design); decide whether Export list and Add a client are wanted.

### F-008 Close account, Home and Portfolio disagree on how many investments are running

- **Area:** Logic
- **Severity:** Medium
- **Affected roles:** Client (verified)
- **Steps:** With two paid investments, one goal saving and one investment awaiting payment: open Home, Portfolio and Profile, Close account.
- **Expected:** The same count everywhere, and a count that matches the amount beside it.
- **Actual:** Close account says "4 investments still running, UGX 1,534,000" (the count includes the unpaid one, the amount does not). Home says "2 active" plus the goal. Portfolio lists 3 running.
- **Evidence:** [close account](screenshots/client-phone/83-account-close.jpg), [home](screenshots/client-phone/30-home.jpg), [portfolio](screenshots/client-phone/34-portfolio.jpg).
- **Suggested fix:** Count only started investments on Close account and name unpaid ones separately; use one shared count for Home and Portfolio.

### F-009 Withdraw offers only a whole early withdrawal

- **Area:** Design match
- **Severity:** Medium
- **Affected roles:** Client (verified)
- **Steps:** Open a running investment, choose Withdraw.
- **Expected:** The canvas (Redeem): choose Interest only, An amount or Everything, and which investment to take it from.
- **Actual:** Only the whole investment can be withdrawn early. No part withdrawal and no source picker.
- **Evidence:** [build](screenshots/client-phone/47-investment-withdraw.jpg) against board Redeem.
- **Suggested fix:** Owner decides whether part withdrawals are wanted before launch; if not, update the canvas.

### F-010 Console has no list of every payment in and out, and Reconciliation has no client money check

- **Area:** Design match
- **Severity:** Medium
- **Affected roles:** Admin, Operations, Finance
- **Steps:** Open Money movement and Reconciliation in the console.
- **Expected:** The canvas: Money movement lists every movement with filters (Deposits, Payouts, Failed, Unmatched); Reconciliation shows "Client money check: what we owe clients equals what we hold", a Needs a person list and Recent days.
- **Actual:** Money movement shows exceptions only (held up, never answered, bank batches, returns). Reconciliation has close-the-day per pot, wrong-client moves and pot transfers, but no single owed-against-held panel and no custodian pot. Staff can only see the full list by downloading the Deposits and withdrawals report.
- **Evidence:** [money movement](screenshots/console-wide-admin/17-money-movement.jpg), [reconciliation](screenshots/console-wide-admin/18-reconciliation.jpg).
- **Suggested fix:** Add a read-only movements table with filters, and the owed-against-held panel (the Overview already computes client money held).

### F-011 The first visit downloads about 1.9 MB of script on the client app and 1.7 MB on the console

- **Area:** Performance
- **Severity:** Medium
- **Affected roles:** Visitor, Client, Staff
- **Steps:** Build both apps and look at the files loaded before the first screen.
- **Expected:** A first load that suits mobile data in Uganda; the data-saving work points the same way.
- **Actual:** Client: `entry` 1.13 MB plus `__common` 0.79 MB (about 490 KB compressed) before Welcome shows. Console: one 1.66 MB file (448 KB compressed) for every screen.
- **Evidence:** Build output in `apps/client/dist/_expo/static/js/web` and the console `assets` folder. Screens opened in 1.4 to 2.5 s locally with no network delay.
- **Suggested fix:** Split the console by screen, and look at what the client's common chunk carries for a first-time visitor (face check and PDF code should load on demand).

### F-012 The yearly rate is shown rounded up, so quotes and the website's estimate disagree

- **Area:** Logic
- **Severity:** Low
- **Affected roles:** Visitor, Client
- **Steps:** Compare the rate label, the app's quote and the website calculator for UGX 1,000,000 over 91 days.
- **Expected:** Figures that agree, or a label that does not overstate.
- **Actual:** 12.5% less 15% tax is 10.625%, shown as 10.63%. The app quotes UGX 26,490 (exact). The website calculator shows UGX 26,502 because it works from the rounded label. The site marks it "About", and `apps/site/src/lib/estimate.ts` documents the gap.
- **Evidence:** `GET /public/quote?product=TREA-91&amount_minor=1000000` gives 26,490; [site home](screenshots/site-wide/10-index.jpg) shows 1,026,502.
- **Suggested fix:** Have the site calculator call the public quote route, or round the label down.

### F-013 Password strength says "Good password" while the leaked-password error is showing

- **Area:** UI
- **Severity:** Low
- **Affected roles:** Visitor
- **Steps:** Sign up and enter password123 as the password.
- **Expected:** The meter should not praise a refused password.
- **Actual:** Error "This password has appeared in a data leak" with the meter still reading Good password.
- **Evidence:** [screenshot](screenshots/client-phone/08-signup-common-password.jpg).
- **Suggested fix:** Reset the meter to weak when the server refuses the password.

### F-014 Terms not ticked is shown by a red box only

- **Area:** Accessibility
- **Severity:** Low
- **Affected roles:** Visitor
- **Steps:** On sign-up leave the terms box unticked and continue.
- **Expected:** A message in words, announced to screen readers.
- **Actual:** The box turns red with no text.
- **Evidence:** [screenshot](screenshots/client-phone/04-signup-terms-not-ticked.jpg).
- **Suggested fix:** Add the error text under the box and tie it to the checkbox.

### F-015 The app asks for a claim on every start and gets a 404

- **Area:** UI
- **Severity:** Low
- **Affected roles:** Client
- **Steps:** Open the app as any client who is not moving from the old records; watch the network.
- **Expected:** No failed request on a normal start.
- **Actual:** `GET /client/claim` answers 404 on every Home load and shows as an error in the browser console.
- **Evidence:** Seen on every crawl of Home.
- **Suggested fix:** Answer 200 with an empty result, or only ask when the session says a claim exists.

### F-016 Pay out screen for a running investment says the money is already on its way

- **Area:** UI
- **Severity:** Low
- **Affected roles:** Client (verified)
- **Steps:** Open /investment/pay-out/{id} for an investment that is still running.
- **Expected:** A message that it can be paid out at maturity, or the early withdrawal path.
- **Actual:** "This money is already on its way to you."
- **Evidence:** [screenshot](screenshots/client-phone/45-investment-pay-out.jpg).
- **Suggested fix:** Show the right state for a running investment.

### F-017 Devices lists "Unknown device" and "Signed in with sign up"

- **Area:** UI
- **Severity:** Low
- **Affected roles:** Client
- **Steps:** Sign up, then open Sign in and security, Devices.
- **Expected:** A device name and a plain way of signing in.
- **Actual:** "Unknown device" and "Signed in with sign up".
- **Evidence:** [screenshot](screenshots/client-phone/70-security-devices.jpg).
- **Suggested fix:** Name the browser or phone from the user agent, and word the method ("When you created your account").

### F-018 Payout limits read "Not set"

- **Area:** UI
- **Severity:** Low
- **Affected roles:** Client, Admin, Operations, Finance
- **Steps:** Open Payout accounts in the app, or Settings in the console.
- **Expected:** A limit, or words that say there is none.
- **Actual:** Mobile money per day, bank per payout and bank per day show "Not set".
- **Evidence:** [app](screenshots/client-phone/65-payout-accounts.jpg), [console](screenshots/console-wide-admin/29-settings.jpg).
- **Suggested fix:** Set the limits before launch, or say "No limit".

### F-019 Three automated accessibility findings in the client app

- **Area:** Accessibility
- **Severity:** Low
- **Affected roles:** Client
- **Steps:** Run axe (WCAG 2.1 AA) on every client screen.
- **Expected:** No violations.
- **Actual:** `aria-prohibited-attr` on the Portfolio split bar (a label on an element with no role); `nested-interactive` on the not-found page; `label-content-name-mismatch` on card summaries on Home, Invest, Insights, Payout accounts, Security and Notifications (this rule is switched off in the repository's own axe tests).
- **Evidence:** Crawl output for 70 client screens on phone and wide.
- **Suggested fix:** Give the split bar `role="img"`; fix with F-006; decide whether card labels should start with their visible text.

### F-020 Opening a later step of a flow directly leaves a dead end

- **Area:** UI
- **Severity:** Low
- **Affected roles:** Client
- **Steps:** Open /goals/start (step 3 of a new goal) without doing steps 1 and 2, then press either button.
- **Expected:** Go back to step 1.
- **Actual:** Target UGX 0 and no name, with live buttons; pressing one shows "Something in this goal is not right. Check it and try again." The server refuses correctly. /goals/plan shows "Your goal". A reload on step 2 of log in starts log in again.
- **Evidence:** [screenshot](screenshots/client-phone-flows/54c-goal-start-deep-link-result.jpg).
- **Suggested fix:** Redirect to the first step when the earlier answers are missing.

### F-021 Console rough edges

- **Area:** UI
- **Severity:** Low
- **Affected roles:** Staff
- **Steps:** Use the console at 1440 by 900 and on a phone.
- **Expected:** As designed.
- **Actual:** The sidebar needs scrolling to reach Rates to Migration at 900 high (the canvas fits all 21). On a phone the Overview "Open" links are 34 by 20 px and table links 21 px high. Dealing desk shows UGX 250,000 as "0.3m". Operations gets a refused `GET /staff/products` on every visit to Investments and Goals. Migration says "Finvest 1.0" although clients were on paper. Settings lists "investment booked" in lower case. Sign out after no activity is 30 minutes (canvas 15). The offline banner in the app shows two Try again buttons.
- **Evidence:** [overview on a phone](screenshots/console-phone-admin/10-overview.jpg), [dealing desk](screenshots/console-wide-admin/22-dealing-desk.jpg).
- **Suggested fix:** Small fixes, one PR.

### F-022 Finance month close is guarded by a view permission

- **Area:** Security
- **Severity:** Low
- **Affected roles:** Admin, Finance
- **Steps:** Read which permission each staff route declares.
- **Expected:** A change permission on routes that change something.
- **Actual:** Proposing, declining, withdrawing and approving a month close all need only `finance.view` (held by Admin and Finance). Approval does need a fresh second step, and the proposer cannot approve. No wrong role can reach it today.
- **Evidence:** `authz-routes` listing from the route sweep.
- **Suggested fix:** Add `finance.close` so a later read-only finance role cannot close months.

### F-023 Website leaves out a few designed items

- **Area:** Design match
- **Severity:** Low
- **Affected roles:** Visitor
- **Steps:** Compare each page with its board.
- **Expected:** As on the canvas.
- **Actual:** No App Store and Google Play buttons, no Careers link, no "Call us" card on Help, no team section on About (known gap). The regulated strip and licence block are covered by F-003.
- **Evidence:** [home](screenshots/site-wide/10-index.jpg), [about](screenshots/site-wide/14-about.jpg), [help](screenshots/site-wide/15-help.jpg).
- **Suggested fix:** Add when the phone line, store listings and team are ready, or remove from the canvas.

### F-024 Client screens differ from the canvas in detail

- **Area:** Design match
- **Severity:** Low
- **Affected roles:** Client
- **Steps:** Compare each client screen with its board (see the design match table).
- **Expected:** As on the canvas.
- **Actual:** The screens exist and follow the same style, but details differ: see each row marked Differs.
- **Evidence:** Design match table.
- **Suggested fix:** Go through the Differs rows with the owner: build, or mark the canvas as changed.

### F-025 Console screens differ from the canvas in detail

- **Area:** Design match
- **Severity:** Low
- **Affected roles:** Staff
- **Steps:** Compare each console screen with its board (see the design match table).
- **Expected:** As on the canvas.
- **Actual:** All 21 screens exist. Details differ: see each row marked Differs.
- **Evidence:** Design match table.
- **Suggested fix:** As F-024.

### F-026 The main button is lime on the canvas and black in several built flows

- **Area:** Design match
- **Severity:** Low
- **Affected roles:** Client
- **Steps:** Compare Product, Maturity choice, Pay and Review with their boards.
- **Expected:** One rule for the main action colour.
- **Actual:** Lime "Invest", "Save my choice" and "Pay" on the canvas; black "Review", "Save" and "Pay UGX …" in the app. Other flows use lime.
- **Evidence:** [product](screenshots/client-phone/39-product-trea-91.jpg), [maturity choice](screenshots/client-phone/44-investment-maturity-choice.jpg).
- **Suggested fix:** Pick one rule and apply it in the shared Button.

### F-027 Forms accept values that are refused later

- **Area:** UI
- **Severity:** Low
- **Affected roles:** Client
- **Steps:** Bank receipt: choose a text file renamed .jpg. Add payout account: type another person's name.
- **Expected:** Tell the client at once.
- **Actual:** The receipt screen shows the file as "ready to send"; the payout account for "Someone Else" is created and waits for a code, to be refused by staff later (names that differ are always refused).
- **Evidence:** [receipt form](screenshots/client-phone-flows/50c-bank-receipt-odd-values.jpg), [payout accounts](screenshots/client-phone/65-payout-accounts.jpg).
- **Suggested fix:** Check the file's first bytes in the app; warn when the typed name differs from the ID name, or lock the name as the canvas does.

### F-028 A password reset accepts the password already in use

- **Area:** Security
- **Severity:** Low
- **Affected roles:** Client
- **Steps:** Reset the password and enter the current one.
- **Expected:** Refuse a password equal to the old one.
- **Actual:** Accepted.
- **Evidence:** Reset flow run as a test client.
- **Suggested fix:** Compare with the current hash on reset.

### F-029 Tablet width uses the phone layout stretched

- **Area:** UI
- **Severity:** Low
- **Affected roles:** Client
- **Steps:** Open the app at 768 px wide.
- **Expected:** A layout that uses the width.
- **Actual:** The phone layout with full-width cards and the bottom tab bar; the wide layout starts at 1024 px. Nothing overflows at 320, 768, 1024, landscape or 200% zoom.
- **Evidence:** Breakpoint run on 12 screens at five sizes.
- **Suggested fix:** Start the wide layout near 768 px, or cap the phone layout's width.

### F-030 Approving a second account on an ID number already in use fails with the wrong message

- **Area:** Logic
- **Severity:** Medium
- **Affected roles:** Operations, Compliance, Admin
- **Steps:** 1. Two people sign up and hand in ID checks with the same ID number. 2. Staff approve the first. 3. Staff open the second on Identity checks, give a reason and Approve.
- **Expected:** The reviewer is told another approved client already holds this ID and to escalate, and the check is flagged so the next reviewer sees it.
- **Actual:** The second approval is correctly refused (409), but the console says "Someone else has already acted on this check. It has been reloaded." The check stays in the queue as Needs review with Approve still offered and no duplicate flag, so every reviewer who opens it meets the same wrong message.
- **Evidence:** [screenshot](screenshots/second-pass/01-console-duplicate-id-wrong-message.jpg). `_approve` in `backend/app/modules/kyc/service.py` returns False when the document is taken and the route answers a plain `conflict`. The `duplicate_document` flag is only set on the automatic path, and the console's own `duplicate_document` message (identity-checks/api.ts) is never reached.
- **Suggested fix:** Answer `conflict` with a `duplicate_document` field code and set the `duplicate_document` review flag on the check when a staff approval meets it; map the code in the console's `toKycError`. Consider flagging it when the check is handed in, so the reviewer knows before deciding.

### F-031 No sensible upper limit on amounts and ages

- **Area:** Logic
- **Severity:** Low
- **Affected roles:** Client
- **Steps:** 1. `POST /client/investments` with `amount_minor` 10,000,000,000,000 (UGX 10 trillion). 2. `POST /client/goals` with a target of UGX 1 quadrillion. 3. ID details with a birth date of 1 January 1890.
- **Expected:** Each is refused, or held for a person, above a figure the business sets.
- **Actual:** All three are accepted. The API's only ceiling on money is 10^15, there for integer safety. The app's amount field accepts up to UGX 999,999,999,999. An unpaid booking of that size counts in the console's Awaiting payment and breaks the Portfolio row layout. Nothing moves until it is paid, so no money is at risk.
- **Evidence:** [screenshot](screenshots/second-pass/02-portfolio-ten-trillion-unpaid.jpg). Checks J2-INV-absurdly-large-amount, J3-GOAL-a-quadrillion-target and J2-KYC-a-birth-date-136-years-ago in section 6.
- **Suggested fix:** Add a largest single investment (per product or in Settings, changed by two people like the other limits), use it for goal targets too, and refuse a birth date more than about 120 years back.

### F-032 Console client record shows internal IDs and a misleading label

- **Area:** UI
- **Severity:** Low
- **Affected roles:** All staff roles
- **Steps:** 1. Console, Clients, find a client with running investments. 2. Find a client who signed in with a code, reset a password or was unfrozen in the last day.
- **Expected:** Holdings are named by product; the 24 hour rule is described as a limit on withdrawals to new accounts.
- **Actual:** "What Finvest holds for them" lists each holding as "Invested" followed by a raw investment ID, with no product or maturity date. The 24 hour withdrawal restriction is labelled "Sign-in held back until", though the client can sign in normally.
- **Evidence:** [holdings](screenshots/second-pass/04-console-client-record-holdings.jpg), [label](screenshots/second-pass/03-console-client-record-labels.jpg). `apps/console/src/screens/clients/Parts.tsx:94`.
- **Suggested fix:** Show the product name and maturity date for each holding, and reword the label to "Withdrawals limited to known accounts until".

### F-033 Small gaps in checks on drafts and proposals

- **Area:** Logic
- **Severity:** Low
- **Affected roles:** Client, Admin, Finance
- **Steps:** 1. `PATCH /client/organisations/{id}` on a draft company with `approvals_needed` 5 and two signatories. 2. Two staff each propose a new product with the same code.
- **Expected:** Both are refused when made.
- **Actual:** The draft keeps 5; the app then shows neither withdrawal rule selected, with a warning. Sending for review is refused, so no account opens that way. Both product proposals sit as Waiting for approval; only one can be approved.
- **Evidence:** [screenshot](screenshots/second-pass/05-company-draft-no-rule-selected.jpg). Checks J2-ORG-APPROVALS and the product proposals in the second pass.
- **Suggested fix:** Refuse `approvals_needed` above the joined signatories on the draft, and refuse a new-product proposal while another open proposal holds the code.

## 4. What could not be tested, and why

| Area | Why |
| --- | --- |
| Production headers and TLS on finvestug.com, app, admin and api | This environment's network policy blocks those hosts (every request was answered 403 by the outbound proxy). The edge configuration in `infra/deployment/Caddyfile` was read instead: HSTS for a year with subdomains, nosniff, frame DENY, a content security policy per host, `.env`, `.git` and `.sql` paths answered 404, and no-index on the API, console and app. Someone with network access should confirm the live headers. |
| Android and iPhone builds | Only the web build of the client app can run here. PIN, biometric unlock, push and camera capture on a phone were not exercised. |
| Passkeys | Screens were opened; a real passkey ceremony needs a device authenticator. Covered by the repository's own tests. |
| Real providers: ioTec (payments, payouts, Verify), Regula or ioTec liveness, Sigma360, Resend, SMS | Sandbox and stub providers only. The stub passes every live check and screening at once. |
| ID photos in the console | Local memory storage gives links a browser cannot open, so the photos show as broken images locally. |
| Bank transfer confirmation, payouts actually sent, maturity runs, interest over time, month close | They need a statement import or days to pass. They are covered by the integration and journey suites, which passed. The screens up to the waiting state were tested by hand. |
| Website statement check form | The local site build calls the production API. The API route itself was tested with a real code (genuine), an unknown code and odd input. |
| Claim an account (moved clients), joint accounts, company and group sign-up beyond the first screen | Need imported or approved organisation records; the screens were opened and compared only. |
| Luganda | Shows Coming soon. |
| Screen reader use by a person | axe was run on every screen; no one listened to the app. Also listed in `docs/gaps.md`. |
| Load and stress | Out of scope by instruction. Only single-user timings and bundle sizes were measured. |
| Emails as delivered | Compared from the committed renders in `docs/screenshots/emails/`; nothing was sent. |

## 5. Top five risks and fix order

1. **F-001 Send never works.** A designed, visible feature fails for every client. One small backend fix and one route test.
2. **F-002 Selfie retake dead end.** With a live check provider on, any client asked for a new selfie is stuck, and staff have no way forward except rejecting the whole check.
3. **F-003 Legal wording and company facts.** Clients would accept terms that are headings only. This waits on the owner and the lawyers, so start it first even though it lands last.
4. **F-004 Statement and references.** The statement is the document clients show to third parties, and its amounts are printed over the reference. Fix together with short references.
5. **F-005 and F-006 Web resilience.** A blank page offline and a framework error page are what a client on a weak connection will meet first.

Then F-008 (counts that disagree on Close account), F-007 and F-010 (console search and money list), and the Low findings in groups.

## 6. Protection checks (defensive, local only)

No exploit payloads, no load, no production. Each check confirms a control exists and refuses harmless odd input.

- **Every route declares who may call it.** Of 493 routes, 37 are open by design (health, sign-in, sign-up, reset, claim, public rates, quote and statement check, provider callbacks, the Resend webhook), 144 need a signed-in client, 304 need a named staff permission and 8 need only a staff session (own session, own devices, menu counts).
- **The running API enforces it.** Calls made: {"anon": 456, "client-on-staff": 312, "staff-on-client": 720, "role-allowed-get": 352, "role-refused": 706, "csrf": 1}. Nobody signed in: all 401. A client token on staff routes: all 401. A staff cookie on client routes: all 401. A role without the permission: all 403. A role with the permission on read routes: none refused, no server error. A cookie with no Origin header, or a foreign one, on a changing request: 403.
- **Routes that decide another person's change** use the fresh second step (`require_confirmed`). The 14 without it reject, withdraw or propose. Two routes guarded by a view permission (`clients.view` for client actions, `settings.view` for platform rules) check a finer permission inside.
- **Stored data.** Passwords are argon2id. Document number, date of birth, TIN, next of kin and authenticator secrets are sealed field by field, as decided; phone, email, names and payout account numbers are plain text behind row policies.
- **Logs.** No phone number, email or name in the API, worker or scheduler logs after the whole run.
- **Bundles.** No secrets and no source maps in the client, console or website builds.

| Check | Area | What was checked | Result |
| --- | --- | --- | --- |
| L-INV-below-minimum | Logic | invest refused: below minimum | Pass |
| L-INV-zero | Logic | invest refused: zero | Pass |
| L-INV-negative | Logic | invest refused: negative | Pass |
| L-INV-float-amount | Logic | invest refused: float amount | Pass |
| L-INV-string-amount | Logic | invest refused: string amount | Pass |
| L-INV-wrong-currency | Logic | invest refused: wrong currency | Pass |
| L-INV-unknown-product | Logic | invest refused: unknown product | Pass |
| L-INV-huge-amount | Logic | invest refused: huge amount | Pass |
| L-IDEM-1 | Logic | invest without Idempotency-Key refused | Pass |
| L-IDEM-2 | Logic | same key twice makes one investment | Pass |
| L-IDEM-3 | Logic | same key, different body refused | Pass |
| S-IDOR-inv | Security | another client reading an investment by id | Pass |
| S-IDOR-pay | Security | another client paying for an investment by id | Pass |
| L-IDEM-4 | Logic | payment retried with same key is one payment | Pass |
| J-PAY-1 | Journey | mobile money payment confirms and investment becomes active | Pass |
| L-PAY-2 | Logic | paying again for an already paid investment refused | Pass |
| S-IDOR-goal | Security | another client reading a goal by id | Pass |
| S-IDOR-plan | Security | another client reading an auto-invest plan | Pass |
| S-IDOR-dest | Security | another client reading a payout account | Pass |
| S-IDOR-dest-del | Security | another client deleting a payout account | Pass |
| S-IDOR-complaint | Security | another client reading a complaint | Pass |
| S-HDR | Security | API response headers | Pass |
| S-CORS1 | Security | CORS preflight from a foreign origin | Pass |
| S-CORS2 | Security | CORS preflight from the client app origin | Pass |
| S-COOKIE | Security | Client session cookie flags (local: Secure off by config) | Pass |
| S-LOGOUT | Security | Signing out ends the session on the server | Pass |
| S-2FA-WRONG | Security | Wrong authenticator code is refused | Pass |
| S-ENUM-LOGIN | Security | Unknown number and wrong password answer the same | Pass |
| S-ENUM-RESET | Security | Password reset start answers the same for unknown and known numbers | Pass |
| S-ENUM-SIGNUP | Security | Sign-up start does not reveal that a number already has an account | Pass |
| S-THROTTLE | Security | Repeated wrong passwords are slowed down | Pass |
| S-TOKENBODY | Security | Web (cookie) login: is the session token also readable by page script in the JSON body? | Pass |
| S-LOGIN-REUSE | Security | A finished login id cannot be used again | Pass |
| S-TOTP-REPLAY | Security | An authenticator code already used is refused inside its 30 seconds | Pass |
| S-UPLOAD-SIZE | Security | A 12 MiB photo is refused before it is read | Pass |
| S-BODY-SIZE | Security | A JSON body over 256 KiB is refused | Pass |
| S-WEBHOOK-RESEND | Security | Resend webhook without a signature is refused | Pass |
| S-CALLBACK | Security | Provider callback without a signature is refused | Pass |
| S-SMS-CALLBACK | Security | SMS delivery callback with a wrong token is refused | Pass |
| MC-PAYOUT-SELF | Logic | Payout account: the person who checked it cannot also approve it | Pass |
| MC-PAYOUT-OTHER | Logic | Payout account: a different person approves | Pass |
| MC-RATE-SELF | Logic | Rate change: the proposer cannot approve their own change | Pass |
| MC-RATE-ROLE | Security | Rate change: Support cannot approve | Pass |
| MC-RATE-OTHER | Logic | Rate change: a different Finance person approves | Pass |
| MC-RATE-TWICE | Logic | Rate change: approving twice does nothing more | Pass |
| MC-RATE-BAD-negative-rate | Logic | Rate proposal with negative rate is refused | Pass |
| MC-RATE-BAD-absurd-rate | Logic | Rate proposal with absurd rate is refused | Pass |
| MC-RATE-BAD-start-date-in-the-past | Logic | Rate proposal with start date in the past is refused | Pass |
| MC-RATE-BAD-tax-over-100% | Logic | Rate proposal with tax over 100% is refused | Pass |
| MC-TRUST-ROLE | Security | Trust account: Operations cannot propose a change | Pass |
| MC-TRUST-SELF | Logic | Trust account: the proposer cannot approve their own change | Pass |
| MC-TRUST-OTHER | Logic | Trust account: a second admin approves and it is in use | Pass |
| J-WD-WAIT | Logic | Early withdrawal to a bank account approved under 24 hours ago, or without the PIN, is refused | Pass |
| J-WD-IDOR | Security | Another client cannot withdraw someone else's investment | Pass |
| J-STMT-PDF | Logic | A requested statement is produced as a PDF | Pass |
| J-STMT-XLSX | Logic | The statement is also offered as a spreadsheet | Pass |
| J-DOC-IDOR | Security | Another client cannot download someone else's statement | Pass |
| J-STMT-BAD-end-before-start | Logic | Statement request end before start is refused | Pass |
| J-STMT-BAD-longer-than-the-look-back-limit | Logic | Statement request longer than the look-back limit is refused | Pass |
| J-STMT-BAD-in-the-future | Logic | Statement request in the future is refused | Pass |
| J-CLOSE-BLOCKED | Logic | Closing an account with running investments is refused, with what is in the way | Pass |
| J-LOOKUP-THROTTLE | Security | Looking up many numbers on Send is slowed down (stops guessing who is a client) | Pass |
| J2-INV-paused-dollar-product | Logic | Invest with paused dollar product is refused | Pass |
| J2-INV-absurdly-large-amount | Logic | Invest UGX 10 trillion is refused (F-031) | Fail |
| J2-INV-fraction-of-a-shilling | Logic | Invest with fraction of a shilling is refused | Pass |
| J2-INV-amount-as-text | Logic | Invest with amount as text is refused | Pass |
| J2-INV-wrong-currency-for-the-product | Logic | Invest with wrong currency for the product is refused | Pass |
| J2-INV-interest-to-a-goal-with-no-goal-named | Logic | Invest with interest to a goal with no goal named is refused | Pass |
| J2-INV-unknown-field | Logic | Invest with unknown field is refused | Pass |
| J2-INV-UNPAID-CAP | Logic | A client cannot pile up unlimited unpaid investments | Pass |
| J2-MAT-IDOR | Security | Another client cannot change someone's maturity choice | Pass |
| J2-MAT-BAD | Logic | Unknown maturity choice is refused | Pass |
| J2-FREEZE | Logic | After Freeze my account the client is locked out at once | Pass |
| J2-FREEZE-LOGIN | Logic | A frozen client cannot log in again until staff unfreeze | Pass |
| J2-CMP-IDOR | Security | Another client cannot read someone's complaint | Pass |
| J2-CMP-SELF | Security | The person who proposed a complaint outcome cannot confirm it | Pass |
| J2-CMP-CONFIRM | Logic | A second person confirms the complaint outcome | Pass |
| J2-CMP-CLIENT-SEES | Logic | The client sees the outcome of their complaint | Pass |
| J2-SUP-REPLY | Logic | The client sees the reply staff wrote | Pass |
| J2-SUP-ISOLATION | Security | A client's chat holds only their own messages | Pass |
| J2-SUP-ROLE | Security | Finance cannot read support chats | Pass |
| J2-UNFREEZE-SELF | Security | Staff cannot approve their own unfreeze | Pass |
| J2-UNFREEZE | Logic | A second person unfreezes the account | Pass |
| J2-UNFREEZE-LOGIN | Logic | The client can log in again after the unfreeze | Pass |
| J2-ACT-pin_reset | Logic | Staff action pin_reset: own approval refused, second person approves | Pass |
| J2-ACT-statement | Logic | Staff action statement: own approval refused, second person approves | Pass |
| J2-ACT-withdrawal_limits | Logic | Staff action withdrawal_limits: own approval refused, second person approves | Pass |
| J2-PROD-a-code-already-in-use | Logic | New product with a code already in use | Pass |
| J2-PROD-a-zero-day-term | Logic | New product with a zero day term | Pass |
| J2-PROD-a-100%-rate | Logic | New product with a 100% rate | Pass |
| J2-PROD-a-zero-minimum | Logic | New product with a zero minimum | Pass |
| J2-PROD-a-dollar-product-while-dollars-are-paused | Logic | New product with a dollar product while dollars are paused | Pass |
| J2-CSV-FORMULA | Security | Every spreadsheet download writes text that starts like a formula as plain text | Pass |
| J2-PIN-RESET-SESSIONS | Security | A staff PIN reset signs the client out everywhere | Pass |
| J2-NOTIF-SECURITY | Logic | Security notices cannot be switched off completely | Pass |
| J2-GRP-NONMEMBER | Security | A non-member cannot read a group goal | Pass |
| J2-GRP-MEMBER-EDIT | Security | An ordinary member cannot rename the group | Pass |
| J2-GRP-MEMBER-CLOSE | Security | An ordinary member cannot close the group | Pass |
| J2-ORG-NONMEMBER | Security | A non-member cannot read a company account | Pass |
| J2-ORG-SUBMIT-EMPTY | Logic | A company cannot be sent for checking without its documents and signatories | Pass |
| J2-ORG-APPROVALS | Logic | A company cannot be sent for checking while it needs more approvals than it has signatories | Pass |
| J2-ORG-APPROVE-DRAFT | Logic | Staff cannot approve a company that was never submitted | Pass |
| J2-SELF-SERVICE-SELFIE | Security | Changing the password or phone number from Profile first asks for a live selfie (or passkey/authenticator confirmation) | Pass |
| J2-EMAIL-BAD | Logic | A malformed email is refused | Pass |
| J2-CSV-FORMULA | Security | Every spreadsheet download writes text that starts like a formula as plain text | Pass |
| J2-PIN-RESET-SESSIONS | Security | A staff PIN reset signs the client out everywhere | Pass |
| J2-KYC-someone-under-18 | Logic | ID details with someone under 18 | Pass |
| J2-KYC-a-birth-date-in-the-future | Logic | ID details with a birth date in the future | Pass |
| J2-KYC-an-ID-that-has-expired | Logic | ID details with an ID that has expired | Pass |
| J2-KYC-markup-in-a-name | Logic | ID details with markup in a name | Pass |
| J2-KYC-a-birth-date-136-years-ago | Logic | ID details with a birth date 136 years ago are refused (F-031) | Fail |
| J2-KYC-NOT-IMAGE | Security | A file that is not really an image is refused as an ID photo | Pass |
| J2-KYC-SVG | Security | An SVG is refused as an ID photo | Pass |
| J3-STMT-an-end-before-the-start | Logic | A statement for an end before the start is refused | Pass |
| J3-STMT-a-period-in-the-future | Logic | A statement for a period in the future is refused | Pass |
| J3-STMT-a-126-year-period | Logic | A statement for a 126 year period is refused | Pass |
| J3-GOAL-a-zero-target | Logic | A goal with a zero target is refused | Pass |
| J3-GOAL-a-negative-target | Logic | A goal with a negative target is refused | Pass |
| J3-GOAL-a-date-in-the-past | Logic | A goal with a date in the past is refused | Pass |
| J3-GOAL-a-blank-name | Logic | A goal with a blank name is refused | Pass |
| J3-GOAL-a-quadrillion-target | Logic | A goal with a UGX 1 quadrillion target is refused (F-031) | Fail |
| J3-SAVE-one-shilling | Logic | Saving one shilling into a goal is refused | Pass |
| J3-SAVE-a-negative-amount | Logic | Saving a negative amount into a goal is refused | Pass |
| J3-SAVE-zero | Logic | Saving zero into a goal is refused | Pass |
| J3-PAYDAY-0 | Logic | Payday 0 is refused | Pass |
| J3-PAYDAY-32 | Logic | Payday 32 is refused | Pass |
| J3-PAYDAY--1 | Logic | Payday -1 is refused | Pass |
| J3-AUTO-an-amount-under-the-product-minimum | Logic | Automatic investing plan with an amount under the product minimum | Pass |
| J3-AUTO-weekday-9 | Logic | Automatic investing plan with weekday 9 | Pass |
| J3-AUTO-the-paused-dollar-product | Logic | Automatic investing plan with the paused dollar product | Pass |
| J3-AUTO-a-foreign-number | Logic | Automatic investing plan with a foreign number | Pass |
| J3-AUTO-day-31 | Logic | Automatic investing plan with day 31 (valid, months are shorter) | Pass |
| J3-NOK | Logic | Six next of kin with bad numbers are refused | Pass |
| J3-RATE-a-start-date-in-the-past | Logic | A rate change with a start date in the past is refused | Pass |
| J3-RATE-a-negative-rate | Logic | A rate change with a negative rate is refused | Pass |
| J3-RATE-a-200%-rate | Logic | A rate change with a 200% rate is refused | Pass |
| J3-RATE-tax-over-100% | Logic | A rate change with tax over 100% is refused | Pass |
| J3-RATE-an-unknown-product | Logic | A rate change with an unknown product is refused | Pass |
| J3-TEAM-BAD-EMAIL | Logic | Inviting staff with a malformed email is refused | Pass |
| J3-TEAM-DUP | Logic | Inviting an email that is already on the team is refused | Pass |
| J3-TEAM-SELF | Security | A role change cannot be approved by the person who proposed it | Pass |
| J3-TEAM-SUBJECT | Security | The person being promoted cannot approve their own promotion | Pass |
| J3-ANN-SELF | Security | An announcement cannot be approved by its author | Pass |
| J2-KYC-DUPLICATE-ID | Logic | A second account cannot be approved on an ID number another approved client holds | Pass |

## 7. How this was run

- Local stack with `FINVEST_PAYMENTS_PROVIDER` sandbox, stub identity and screening, memory storage and console SMS, in a separate database so the integration suite could run beside it.
- Ten staff accounts (two or three per role) through the real enrol link, and five clients through the real sign-up and ID check.
- Playwright with Chromium at 390 by 844 and 1440 by 900 for every screen, plus 320, 768, 1024, landscape and 200% zoom on twelve key screens; axe on WCAG 2.1 AA.
- All test data is removed by dropping the `finvest_qa` database when the testing ends.

