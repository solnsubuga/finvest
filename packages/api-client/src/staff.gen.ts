// Generated from openapi.json by scripts/generate.ts. Do not edit.
/* eslint-disable */
import { z } from "zod";

/** A file for a multipart upload: a Blob on the web, a local file description on a phone. */
export type Upload = Blob | { uri: string; name: string; type: string };
export const Upload = z.custom<Upload>((value) => value !== null && typeof value === "object");

export const AccountView = z.object({
  created_at: z.string(),
  email: z.string().nullable(),
  has_authenticator: z.boolean(),
  has_passkey: z.boolean(),
  id: z.string(),
  last_signed_in_at: z.string().nullable(),
  phone: z.string(),
  restricted_until: z.string().nullable(),
  status: z.enum(["active", "locked", "closed"]),
});
export type AccountView = z.infer<typeof AccountView>;

export const Action = z.enum(["approve", "send_back", "withdraw", "record_result", "confirm_result", "reject_result"]);
export type Action = z.infer<typeof Action>;

export const Currency = z.enum(["UGX", "USD"]);
export type Currency = z.infer<typeof Currency>;

export const AmountOut = z.object({
  currency: Currency,
  minor: z.number().int(),
});
export type AmountOut = z.infer<typeof AmountOut>;

export const LotOut = z.object({
  allocated: AmountOut,
  cost: AmountOut,
  custodian_reference: z.string(),
  face: AmountOut,
  investments: z.number().int(),
  lot_id: z.string(),
  matures_on: z.string(),
  settled_on: z.string(),
  unallocated: AmountOut,
});
export type LotOut = z.infer<typeof LotOut>;

export const ProductAllocationOut = z.object({
  allocated: AmountOut,
  cost_held: AmountOut,
  covered: AmountOut,
  investments: z.number().int(),
  investments_waiting: z.number().int(),
  lots: z.array(LotOut),
  owed: AmountOut,
  product_code: z.string(),
  unallocated: AmountOut,
  waiting: AmountOut,
});
export type ProductAllocationOut = z.infer<typeof ProductAllocationOut>;

export const AllocationReportOut = z.object({
  currency: Currency,
  on: z.string(),
  products: z.array(ProductAllocationOut),
});
export type AllocationReportOut = z.infer<typeof AllocationReportOut>;

export const AmountOutput = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type AmountOutput = z.infer<typeof AmountOutput>;

export const AnnouncementView = z.object({
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  message: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  status: z.enum(["proposed", "approved", "rejected", "withdrawn"]),
  title: z.string(),
  told: z.number().int(),
  yours: z.boolean(),
});
export type AnnouncementView = z.infer<typeof AnnouncementView>;

export const AnnouncementList = z.object({
  announcements: z.array(AnnouncementView),
});
export type AnnouncementList = z.infer<typeof AnnouncementList>;

export const Profile = z.enum(["careful", "balanced", "long_term"]);
export type Profile = z.infer<typeof Profile>;

export const AnswersView = z.object({
  answered_at: z.string(),
  answers: z.record(z.string(), z.unknown()),
  due_again_at: z.string(),
  id: z.string(),
  longest_term_days: z.number().int().nullable(),
  profile: Profile,
});
export type AnswersView = z.infer<typeof AnswersView>;

export const Approval = z.object({
  reason: z.string(),
});
export type Approval = z.infer<typeof Approval>;

export const ApprovalItemOut = z.object({
  can_decide: z.boolean(),
  id: z.string(),
  kind: z.enum(["wording_change", "ledger_correction", "pot_transfer", "bank_transfer_match", "day_close", "dealing_order", "dealing_result", "buffer_rules", "buffer_capital", "rate_change", "migration_totals", "migration_sign_off", "phone_change", "team_change", "client_lock", "suppression_lift", "complaint_outcome", "announcement", "payout_account", "investment_correction", "investment_run", "investment_booking", "payout_resolution", "tax_remittance", "deposit_reassignment", "payout_release", "investment_transfer", "organisation", "account_closure", "compliance_case", "bank_account_change"]),
  proposed_at: z.string(),
  proposed_by: z.string(),
  proposed_by_name: z.string().nullable(),
  subject: z.string(),
  yours: z.boolean(),
});
export type ApprovalItemOut = z.infer<typeof ApprovalItemOut>;

export const ApprovalsOut = z.object({
  items: z.array(ApprovalItemOut),
});
export type ApprovalsOut = z.infer<typeof ApprovalsOut>;

export const ApproveIn = z.object({
  note: z.string().nullable().optional(),
});
export type ApproveIn = z.infer<typeof ApproveIn>;

export const PhotoKind = z.enum(["front", "back", "selfie"]);
export type PhotoKind = z.infer<typeof PhotoKind>;

export const AskForPhotosBody = z.object({
  client_message: z.string(),
  photos: z.array(PhotoKind),
});
export type AskForPhotosBody = z.infer<typeof AskForPhotosBody>;

export const AssignComplaint = z.object({
  owner_id: z.string(),
});
export type AssignComplaint = z.infer<typeof AssignComplaint>;

export const AtMaturity = z.enum(["roll_over", "pay_out", "pay_interest"]);
export type AtMaturity = z.infer<typeof AtMaturity>;

export const AuthenticatorSetupView = z.object({
  secret: z.string(),
  uri: z.string(),
});
export type AuthenticatorSetupView = z.infer<typeof AuthenticatorSetupView>;

export const AutomaticCheck = z.enum(["document_genuine", "selfie_match", "screening", "liveness"]);
export type AutomaticCheck = z.infer<typeof AutomaticCheck>;

export const BankAccountText = z.object({
  account_name: z.string(),
  account_number: z.string(),
  bank_name: z.string(),
  branch: z.string().nullable().optional(),
});
export type BankAccountText = z.infer<typeof BankAccountText>;

export const BankAccountProposalView = z.object({
  account: BankAccountText,
  currency: z.enum(["UGX", "USD"]),
  id: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  yours: z.boolean(),
});
export type BankAccountProposalView = z.infer<typeof BankAccountProposalView>;

export const CurrencyBankAccountView = z.object({
  changed_at: z.string().nullable(),
  currency: z.enum(["UGX", "USD"]),
  in_use: BankAccountText.nullable(),
  proposal: BankAccountProposalView.nullable(),
});
export type CurrencyBankAccountView = z.infer<typeof CurrencyBankAccountView>;

export const BankAccountList = z.object({
  accounts: z.array(CurrencyBankAccountView),
});
export type BankAccountList = z.infer<typeof BankAccountList>;

export const MoneyView = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type MoneyView = z.infer<typeof MoneyView>;

export const BankPayoutOut = z.object({
  amount: MoneyView,
  business_date: z.string(),
  client_id: z.string(),
  created_at: z.string(),
  destination_id: z.string(),
  id: z.string(),
  status: z.enum(["queued", "sent", "paid", "failed"]),
});
export type BankPayoutOut = z.infer<typeof BankPayoutOut>;

export const ReceiptView = z.object({
  bank_name: z.string(),
  byte_size: z.number().int(),
  content_type: z.string(),
  sent_on: z.string(),
  transaction_number: z.string(),
});
export type ReceiptView = z.infer<typeof ReceiptView>;

export const BankTransferInQueue = z.object({
  amount: MoneyView,
  awaiting: z.enum(["check", "second_person", "posting"]),
  confirmed_at: z.string().nullable(),
  created_at: z.string(),
  failure: z.string().nullable(),
  id: z.string(),
  investment_id: z.string(),
  method: z.enum(["mtn", "airtel", "bank_transfer"]),
  payer: z.string().nullable(),
  receipt: ReceiptView.nullable(),
  status: z.enum(["created", "sending", "awaiting_approval", "uncertain", "in_review", "confirmed", "failed"]),
});
export type BankTransferInQueue = z.infer<typeof BankTransferInQueue>;

export const PaymentView = z.object({
  amount: MoneyView,
  confirmed_at: z.string().nullable(),
  created_at: z.string(),
  failure: z.string().nullable(),
  id: z.string(),
  investment_id: z.string(),
  method: z.enum(["mtn", "airtel", "bank_transfer"]),
  payer: z.string().nullable(),
  receipt: ReceiptView.nullable(),
  status: z.enum(["created", "sending", "awaiting_approval", "uncertain", "in_review", "confirmed", "failed"]),
});
export type PaymentView = z.infer<typeof PaymentView>;

export const ReceiptLinkOut = z.object({
  expires_at: z.string(),
  url: z.string(),
});
export type ReceiptLinkOut = z.infer<typeof ReceiptLinkOut>;

export const ReviewOut = z.object({
  approved_at: z.string().nullable(),
  approved_by: z.string().nullable(),
  outcome: z.enum(["matched", "not_matched"]),
  reason: z.string().nullable(),
  reviewed_at: z.string(),
  reviewed_by: z.string(),
  statement_line_id: z.string().nullable(),
});
export type ReviewOut = z.infer<typeof ReviewOut>;

export const StatementLineOut = z.object({
  account_reference: z.string(),
  amount: MoneyView,
  id: z.string(),
  narrative: z.string(),
  reference: z.string(),
  value_date: z.string(),
});
export type StatementLineOut = z.infer<typeof StatementLineOut>;

export const BankTransferOut = z.object({
  candidates: z.array(StatementLineOut),
  client_id: z.string(),
  matched_line: StatementLineOut.nullable(),
  payment: PaymentView,
  receipt_link: ReceiptLinkOut.nullable(),
  review: ReviewOut.nullable(),
});
export type BankTransferOut = z.infer<typeof BankTransferOut>;

export const BankTransferPage = z.object({
  items: z.array(BankTransferInQueue),
  next_before: z.string().nullable(),
});
export type BankTransferPage = z.infer<typeof BankTransferPage>;

export const BatchIn = z.object({
  currency: z.enum(["UGX", "USD"]),
  payout_ids: z.array(z.string()),
  reason: z.string(),
});
export type BatchIn = z.infer<typeof BatchIn>;

export const BatchItemOut = z.object({
  amount: MoneyView,
  bank_name: z.string().nullable(),
  client_id: z.string(),
  holder_name: z.string(),
  masked_account: z.string(),
  payout_id: z.string(),
});
export type BatchItemOut = z.infer<typeof BatchItemOut>;

export const BatchOut = z.object({
  currency: z.enum(["UGX", "USD"]),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  items: z.array(BatchItemOut).nullable().optional(),
  payout_ids: z.array(z.string()),
  prepared_at: z.string(),
  prepared_by: z.string(),
  reason: z.string(),
  status: z.enum(["prepared", "approved", "declined", "withdrawn"]),
  total: MoneyView,
});
export type BatchOut = z.infer<typeof BatchOut>;

export const BillsOut = z.object({
  cost: AmountOut,
  custodian_reference: z.string(),
  lot_id: z.string(),
  matures_on: z.string(),
  product_code: z.string(),
});
export type BillsOut = z.infer<typeof BillsOut>;

export const Blocker = z.enum(["investments_running", "money_owed", "deposits_in_progress", "payouts_in_progress", "goals_open", "plans_live"]);
export type Blocker = z.infer<typeof Blocker>;

export const BodyImportStatementStaffReconciliationStatementsPost = z.object({
  account_reference: z.string(),
  closing_balance: z.string(),
  file: Upload,
  format: z.literal("standard").optional(),
  opening_balance: z.string(),
  source: z.enum(["iotec", "bank"]),
  statement_date: z.string(),
});
export type BodyImportStatementStaffReconciliationStatementsPost = z.infer<typeof BodyImportStatementStaffReconciliationStatementsPost>;

export const BookLineOut = z.object({
  closing: AmountOut,
  code: z.string(),
  credits: AmountOut,
  debits: AmountOut,
  kind: z.string(),
  name: z.string(),
  opening: AmountOut,
});
export type BookLineOut = z.infer<typeof BookLineOut>;

export const BookingIn = z.object({
  amount_minor: z.number().int(),
  at_maturity: AtMaturity.nullable().optional(),
  client_id: z.string(),
  currency: Currency,
  product_code: z.string(),
  reason: z.string(),
});
export type BookingIn = z.infer<typeof BookingIn>;

export const BookingOut = z.object({
  amount_minor: z.number().int(),
  at_maturity: AtMaturity.nullable(),
  client_id: z.string(),
  currency: Currency,
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_note: z.string().nullable(),
  failure_code: z.string().nullable(),
  finished_at: z.string().nullable(),
  id: z.string(),
  investment_id: z.string().nullable(),
  product_code: z.string(),
  product_name: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  status: z.enum(["proposed", "approved", "sent_back", "withdrawn", "done", "failed"]),
});
export type BookingOut = z.infer<typeof BookingOut>;

export const BufferLevel = z.enum(["on_target", "under_target", "below_floor"]);
export type BufferLevel = z.infer<typeof BufferLevel>;

export const BufferOut = z.object({
  after_promised: AmountOut,
  balance: AmountOut,
  floor: AmountOut,
  from_buffer_up_to: AmountOut,
  level: BufferLevel,
  pay_within_working_days: z.number().int(),
  short: AmountOut,
  target: AmountOut,
  to_raise: AmountOut,
});
export type BufferOut = z.infer<typeof BufferOut>;

export const RulesOut = z.object({
  effective_from: z.string(),
  floor: AmountOut,
  from_buffer_up_to: AmountOut,
  pay_within_working_days: z.number().int(),
  target: AmountOut,
});
export type RulesOut = z.infer<typeof RulesOut>;

export const ProposalStatus = z.enum(["proposed", "approved", "sent_back", "withdrawn"]);
export type ProposalStatus = z.infer<typeof ProposalStatus>;

export const DealingBufferApiStep = z.enum(["approve", "send_back", "decline", "withdraw"]);
export type DealingBufferApiStep = z.infer<typeof DealingBufferApiStep>;

export const RulesProposalOut = z.object({
  allowed: z.array(DealingBufferApiStep),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_note: z.string().nullable(),
  id: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  rules: RulesOut,
  status: ProposalStatus,
});
export type RulesProposalOut = z.infer<typeof RulesProposalOut>;

export const Direction = z.enum(["in", "out"]);
export type Direction = z.infer<typeof Direction>;

export const MovementStatus = z.enum(["proposed", "approved", "declined"]);
export type MovementStatus = z.infer<typeof MovementStatus>;

export const DealingBufferApiMovementOut = z.object({
  allowed: z.array(DealingBufferApiStep),
  amount: AmountOut,
  bank_reference: z.string().nullable(),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  direction: Direction,
  id: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  status: MovementStatus,
  value_date: z.string().nullable(),
});
export type DealingBufferApiMovementOut = z.infer<typeof DealingBufferApiMovementOut>;

export const BufferScreenOut = z.object({
  buffer: AmountOut,
  currency: Currency,
  finvest_money: AmountOut,
  in_force: RulesOut,
  level: BufferLevel,
  movements: z.array(DealingBufferApiMovementOut),
  proposals: z.array(RulesProposalOut),
  versions: z.array(RulesOut),
});
export type BufferScreenOut = z.infer<typeof BufferScreenOut>;

export const BulkDebitLineOut = z.object({
  amount: MoneyView,
  id: z.string(),
  narrative: z.string(),
  reference: z.string(),
  value_date: z.string(),
});
export type BulkDebitLineOut = z.infer<typeof BulkDebitLineOut>;

export const BulkDebitOut = z.object({
  batch: BatchOut,
  lines: z.array(BulkDebitLineOut),
  payouts: z.array(BankPayoutOut),
});
export type BulkDebitOut = z.infer<typeof BulkDebitOut>;

export const BulkIn = z.object({
  payout_ids: z.array(z.string()),
  reason: z.string(),
  statement_line_id: z.string(),
});
export type BulkIn = z.infer<typeof BulkIn>;

export const BulkOut = z.object({
  batch_id: z.string(),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  line: BulkDebitLineOut.nullable(),
  payout_ids: z.array(z.string()),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  statement_line_id: z.string(),
  status: z.enum(["proposed", "approved", "declined", "withdrawn"]),
  total: MoneyView,
});
export type BulkOut = z.infer<typeof BulkOut>;

export const CaseAmountOut = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type CaseAmountOut = z.infer<typeof CaseAmountOut>;

export const CaseProposalOut = z.object({
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decided_by_name: z.string().nullable(),
  decision_note: z.string().nullable(),
  id: z.string(),
  outcome: z.enum(["not_a_match", "pep_accepted", "sanctioned", "no_concern", "report_to_fia"]),
  proposed_at: z.string(),
  proposed_by: z.string(),
  proposed_by_name: z.string(),
  reason: z.string(),
  status: z.enum(["proposed", "approved", "sent_back", "withdrawn"]),
});
export type CaseProposalOut = z.infer<typeof CaseProposalOut>;

export const ClientOut = z.object({
  account_number: z.string().nullable(),
  id: z.string(),
  name: z.string().nullable(),
});
export type ClientOut = z.infer<typeof ClientOut>;

export const CaseOut = z.object({
  amount: CaseAmountOut.nullable(),
  client: ClientOut,
  closed_at: z.string().nullable(),
  code: z.string(),
  id: z.string(),
  opened_at: z.string(),
  outcome: z.enum(["not_a_match", "pep_accepted", "sanctioned", "no_concern", "report_to_fia"]).nullable(),
  source: z.enum(["screening", "monitoring"]),
  status: z.enum(["open", "closed"]),
  waiting: CaseProposalOut.nullable(),
});
export type CaseOut = z.infer<typeof CaseOut>;

export const MatchOut = z.object({
  category: z.enum(["sanctions", "pep", "adverse_media", "other"]),
  entity_id: z.string(),
  known: z.boolean(),
  name: z.string(),
  sources: z.array(z.string()),
  strength: z.number().int(),
});
export type MatchOut = z.infer<typeof MatchOut>;

export const RunOut = z.object({
  failure_code: z.string().nullable(),
  finished_at: z.string().nullable(),
  id: z.string(),
  match_count: z.number().int().nullable(),
  new_match_count: z.number().int().nullable(),
  provider: z.string().nullable(),
  provider_reference: z.string().nullable(),
  reason: z.enum(["onboarding", "rescreen", "payout", "staff"]),
  requested_at: z.string(),
  requested_by: z.string().nullable(),
  status: z.enum(["pending", "clear", "possible_match", "unavailable", "not_run", "no_subject"]),
});
export type RunOut = z.infer<typeof RunOut>;

export const StandingOut = z.object({
  case_id: z.string().nullable(),
  outcome: z.enum(["clear", "possible_match", "confirmed_match", "unavailable", "pending", "not_run"]),
  pep: z.boolean(),
  screened_at: z.string().nullable(),
});
export type StandingOut = z.infer<typeof StandingOut>;

export const CaseDetailOut = z.object({
  can_decide: z.boolean(),
  can_propose: z.boolean(),
  case: CaseOut,
  detail: z.record(z.string(), z.unknown()),
  matches: z.array(MatchOut),
  outcomes: z.array(z.enum(["not_a_match", "pep_accepted", "sanctioned", "no_concern", "report_to_fia"])),
  proposals: z.array(CaseProposalOut),
  run: RunOut.nullable(),
  standing: StandingOut.nullable(),
  yours: z.boolean(),
});
export type CaseDetailOut = z.infer<typeof CaseDetailOut>;

export const CaseProposeIn = z.object({
  outcome: z.enum(["not_a_match", "pep_accepted", "sanctioned", "no_concern", "report_to_fia"]),
  reason: z.string(),
});
export type CaseProposeIn = z.infer<typeof CaseProposeIn>;

export const ChangeView = z.object({
  about_you: z.boolean(),
  current_role: z.enum(["admin", "operations", "finance", "compliance", "support"]).nullable(),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  email: z.string().nullable(),
  id: z.string(),
  kind: z.enum(["invite", "change_role", "deactivate", "reactivate", "reset_sign_in"]),
  proposed_at: z.string(),
  proposed_by: z.string(),
  proposed_by_name: z.string(),
  reason: z.string(),
  role: z.enum(["admin", "operations", "finance", "compliance", "support"]).nullable(),
  staff_id: z.string(),
  staff_name: z.string(),
  status: z.enum(["proposed", "approved", "rejected", "withdrawn"]),
  yours: z.boolean(),
});
export type ChangeView = z.infer<typeof ChangeView>;

export const CheckOut = z.object({
  checked_at: z.string(),
  checked_by: z.string(),
  name_match: z.enum(["exact", "partial"]),
  proof_name: z.string(),
  statement_date: z.string().nullable(),
});
export type CheckOut = z.infer<typeof CheckOut>;

export const CheckStatus = z.enum(["draft", "submitted", "needs_review", "with_compliance", "awaiting_client", "approved", "rejected"]);
export type CheckStatus = z.infer<typeof CheckStatus>;

export const ClaimCheckStatus = z.enum(["draft", "needs_review", "awaiting_client", "matched", "not_matched"]);
export type ClaimCheckStatus = z.infer<typeof ClaimCheckStatus>;

export const ClaimProgressView = z.object({
  cannot_be_invited: z.number().int(),
  claimed_and_confirmed: z.number().int(),
  invited: z.number().int(),
  not_yet_claimed: z.number().int(),
  said_something_is_wrong: z.number().int(),
});
export type ClaimProgressView = z.infer<typeof ClaimProgressView>;

export const StaffPhoto = z.object({
  id: z.string(),
  kind: PhotoKind,
  live: z.boolean().optional(),
});
export type StaffPhoto = z.infer<typeof StaffPhoto>;

export const StaffClaimCheck = z.object({
  check_id: z.string(),
  client_id: z.string(),
  client_message: z.string().nullable(),
  decided_at: z.string().nullable(),
  id: z.string(),
  photos: z.array(StaffPhoto),
  retake_photos: z.array(PhotoKind),
  status: ClaimCheckStatus,
  submission: z.number().int(),
  submitted_at: z.string().nullable(),
});
export type StaffClaimCheck = z.infer<typeof StaffClaimCheck>;

export const ClaimQueue = z.object({
  items: z.array(StaffClaimCheck),
});
export type ClaimQueue = z.infer<typeof ClaimQueue>;

export const ClientCardView = z.object({
  account_number: z.string().nullable(),
  created_at: z.string(),
  id: z.string(),
  status: z.enum(["active", "locked", "closed"]),
  verified_name: z.string().nullable(),
});
export type ClientCardView = z.infer<typeof ClientCardView>;

export const ClientCards = z.object({
  cards: z.array(ClientCardView),
});
export type ClientCards = z.infer<typeof ClientCards>;

export const ConversationSummary = z.object({
  id: z.string(),
  last_message_at: z.string(),
  status: z.string(),
  topic: z.string(),
  waiting_since: z.string().nullable(),
});
export type ConversationSummary = z.infer<typeof ConversationSummary>;

export const DestinationView = z.object({
  bank_code: z.string().nullable(),
  created_at: z.string(),
  holder_name: z.string(),
  id: z.string(),
  kind: z.string(),
  masked_account: z.string(),
  network: z.string().nullable(),
  status: z.string(),
});
export type DestinationView = z.infer<typeof DestinationView>;

export const IdentityCheckView = z.object({
  account_number: z.string().nullable(),
  decided_at: z.string().nullable(),
  id: z.string(),
  status: z.string(),
  submitted_at: z.string().nullable(),
});
export type IdentityCheckView = z.infer<typeof IdentityCheckView>;

export const LockView = z.object({
  client_id: z.string(),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decided_by_name: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  kind: z.enum(["lock", "unlock"]),
  proposed_at: z.string(),
  proposed_by: z.string(),
  proposed_by_name: z.string(),
  reason: z.string(),
  status: z.enum(["proposed", "approved", "rejected", "withdrawn"]),
  yours: z.boolean(),
});
export type LockView = z.infer<typeof LockView>;

export const OwedView = z.object({
  account_code: z.string(),
  account_reference: z.string().nullable(),
  amount_minor: z.number().int(),
  currency: z.string(),
});
export type OwedView = z.infer<typeof OwedView>;

export const ConsoleApiComplaintSummary = z.object({
  category: z.string(),
  due_on: z.string(),
  id: z.string(),
  overdue: z.boolean(),
  received_at: z.string(),
  reference: z.string(),
  status: z.string(),
  summary: z.string(),
});
export type ConsoleApiComplaintSummary = z.infer<typeof ConsoleApiComplaintSummary>;

export const ClientPageView = z.object({
  account: AccountView,
  can_lock: z.boolean(),
  complaints: z.array(ConsoleApiComplaintSummary).nullable(),
  conversation: ConversationSummary.nullable(),
  destinations: z.array(DestinationView),
  identity_check: IdentityCheckView.nullable(),
  locks: z.array(LockView),
  owed: z.array(OwedView),
  verified_name: z.string().nullable(),
});
export type ClientPageView = z.infer<typeof ClientPageView>;

export const ClientScreeningOut = z.object({
  cases: z.array(CaseOut),
  client: ClientOut,
  runs: z.array(RunOut),
  standing: StandingOut.nullable(),
});
export type ClientScreeningOut = z.infer<typeof ClientScreeningOut>;

export const CloseOut = z.object({
  decided_by: z.string().nullable(),
  id: z.string(),
  prepared_by: z.string(),
  status: z.enum(["prepared", "closed"]),
});
export type CloseOut = z.infer<typeof CloseOut>;

export const ComplaintHistoryItem = z.object({
  action: z.enum(["logged", "assigned", "status", "note", "escalated", "proposed", "rejected", "withdrawn", "closed"]),
  actor: z.enum(["client", "staff"]),
  actor_id: z.string(),
  from_status: z.enum(["new", "investigating", "waiting_for_client", "escalated", "closed"]).nullable(),
  id: z.string(),
  note: z.string().nullable(),
  occurred_at: z.string(),
  to_status: z.enum(["new", "investigating", "waiting_for_client", "escalated", "closed"]).nullable(),
});
export type ComplaintHistoryItem = z.infer<typeof ComplaintHistoryItem>;

export const PendingOutcome = z.object({
  client_accepted: z.boolean(),
  id: z.string(),
  outcome: z.enum(["upheld", "partly_upheld", "not_upheld"]),
  proposed_at: z.string(),
  proposed_by: z.string(),
  resolution: z.string(),
});
export type PendingOutcome = z.infer<typeof PendingOutcome>;

export const StaffComplaint = z.object({
  amount: AmountOutput.nullable(),
  category: z.enum(["payments", "withdrawals", "investments", "identity", "account", "fees", "service", "other"]),
  client_accepted: z.boolean().nullable(),
  client_id: z.string(),
  closed_at: z.string().nullable(),
  conversation_id: z.string().nullable(),
  details: z.string(),
  due_on: z.string(),
  id: z.string(),
  outcome: z.enum(["upheld", "partly_upheld", "not_upheld"]).nullable(),
  overdue: z.boolean(),
  owner_id: z.string().nullable(),
  received_at: z.string(),
  received_via: z.enum(["app", "chat", "phone", "email", "in_person", "letter"]),
  reference: z.string(),
  resolution: z.string().nullable(),
  status: z.enum(["new", "investigating", "waiting_for_client", "escalated", "closed"]),
  summary: z.string(),
});
export type StaffComplaint = z.infer<typeof StaffComplaint>;

export const ComplaintDetail = z.object({
  complaint: StaffComplaint,
  history: z.array(ComplaintHistoryItem),
  pending_outcome: PendingOutcome.nullable(),
});
export type ComplaintDetail = z.infer<typeof ComplaintDetail>;

export const ComplaintNote = z.object({
  note: z.string(),
});
export type ComplaintNote = z.infer<typeof ComplaintNote>;

export const ConfirmCode = z.object({
  code: z.string(),
});
export type ConfirmCode = z.infer<typeof ConfirmCode>;

export const ConfirmWithPasskey = z.object({
  credential: z.record(z.string(), z.unknown()),
});
export type ConfirmWithPasskey = z.infer<typeof ConfirmWithPasskey>;

export const Confirmed = z.object({
  confirmed_until: z.string(),
});
export type Confirmed = z.infer<typeof Confirmed>;

export const MigrationApiAmount = z.object({
  currency: Currency,
  minor: z.number().int(),
});
export type MigrationApiAmount = z.infer<typeof MigrationApiAmount>;

export const ControlTotalsRequest = z.object({
  as_of: z.string(),
  clients: z.number().int(),
  interest_before_tax: z.array(MigrationApiAmount),
  invested: z.array(MigrationApiAmount),
  pending_orders: z.number().int(),
  running_investments: z.number().int(),
  verified_identities: z.number().int(),
});
export type ControlTotalsRequest = z.infer<typeof ControlTotalsRequest>;

export const ConversationItem = z.object({
  created_at: z.string(),
  id: z.string(),
  last_message_at: z.string(),
  status: z.enum(["open", "solved"]),
  topic: z.enum(["deposit", "withdrawal", "investment", "identity", "account", "other"]),
  waiting_since: z.string().nullable(),
});
export type ConversationItem = z.infer<typeof ConversationItem>;

export const CorrectionKind = z.enum(["unwind", "refund", "pay_out"]);
export type CorrectionKind = z.infer<typeof CorrectionKind>;

export const CorrectionIn = z.object({
  destination_id: z.string().nullable().optional(),
  investment_id: z.string(),
  kind: CorrectionKind,
  reason: z.string(),
});
export type CorrectionIn = z.infer<typeof CorrectionIn>;

export const CorrectionStatus = z.enum(["proposed", "approved", "sent_back", "withdrawn", "done", "failed"]);
export type CorrectionStatus = z.infer<typeof CorrectionStatus>;

export const CorrectionOut = z.object({
  amount_minor: z.number().int(),
  client_id: z.string(),
  currency: Currency,
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_note: z.string().nullable(),
  destination_id: z.string().nullable(),
  done_at: z.string().nullable(),
  entry_id: z.string().nullable(),
  failure_code: z.string().nullable(),
  id: z.string(),
  investment_id: z.string(),
  kind: CorrectionKind,
  payout_id: z.string().nullable(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  status: CorrectionStatus,
});
export type CorrectionOut = z.infer<typeof CorrectionOut>;

export const Cover = z.enum(["covered", "bid_needed", "on_order", "surplus"]);
export type Cover = z.infer<typeof Cover>;

export const MoneyOut = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type MoneyOut = z.infer<typeof MoneyOut>;

export const LineOut = z.object({
  amount: MoneyOut,
  counterparty_hint: z.string(),
  direction: z.enum(["in", "out"]),
  id: z.string(),
  reference: z.string(),
  suggested_movements: z.array(z.string()),
  value_date: z.string(),
});
export type LineOut = z.infer<typeof LineOut>;

export const ReconciliationApiMovementOut = z.object({
  amount: MoneyOut,
  direction: z.enum(["in", "out"]),
  effective_date: z.string(),
  entry_id: z.string(),
  entry_type: z.string(),
  id: z.string(),
  line_no: z.number().int(),
  reference: z.string(),
});
export type ReconciliationApiMovementOut = z.infer<typeof ReconciliationApiMovementOut>;

export const ExceptionOut = z.object({
  difference: MoneyOut,
  kind: z.enum(["missing_in_ledger", "missing_in_statement", "amount_differs", "direction_differs", "duplicate_reference"]),
  movements: z.array(ReconciliationApiMovementOut),
  statement_lines: z.array(LineOut),
});
export type ExceptionOut = z.infer<typeof ExceptionOut>;

export const ReconciliationApiPositionOut = z.object({
  account_reference: z.string(),
  exceptions: z.array(ExceptionOut),
  ledger_closing: MoneyOut,
  name: z.string(),
  open_exceptions_count: z.number().int(),
  open_exceptions_total: MoneyOut,
  source: z.enum(["iotec", "bank"]),
  statement_closing: MoneyOut.nullable(),
  unexplained: MoneyOut.nullable(),
});
export type ReconciliationApiPositionOut = z.infer<typeof ReconciliationApiPositionOut>;

export const DayOut = z.object({
  blockers: z.array(z.string()),
  business_date: z.string(),
  close: CloseOut.nullable(),
  last_closed: z.string().nullable(),
  positions: z.array(ReconciliationApiPositionOut),
});
export type DayOut = z.infer<typeof DayOut>;

export const DaysOut = z.object({
  date_from: z.string(),
  date_to: z.string(),
});
export type DaysOut = z.infer<typeof DaysOut>;

export const DecideAnnouncement = z.object({
  decision: z.enum(["approved", "rejected", "withdrawn"]),
  reason: z.string().nullable().optional(),
});
export type DecideAnnouncement = z.infer<typeof DecideAnnouncement>;

export const DecideBankAccount = z.object({
  decision: z.enum(["approved", "rejected", "withdrawn"]),
  reason: z.string(),
});
export type DecideBankAccount = z.infer<typeof DecideBankAccount>;

export const DecideChange = z.object({
  decision: z.enum(["approved", "rejected", "withdrawn"]),
  reason: z.string(),
});
export type DecideChange = z.infer<typeof DecideChange>;

export const DecideIn = z.object({
  reason: z.string(),
});
export type DecideIn = z.infer<typeof DecideIn>;

export const DecideLift = z.object({
  decision: z.enum(["approved", "rejected", "withdrawn"]),
  reason: z.string(),
});
export type DecideLift = z.infer<typeof DecideLift>;

export const DecideLock = z.object({
  decision: z.enum(["approved", "rejected", "withdrawn"]),
  reason: z.string(),
});
export type DecideLock = z.infer<typeof DecideLock>;

export const DecideOutcome = z.object({
  decision: z.enum(["confirmed", "rejected", "withdrawn"]),
  proposal_id: z.string(),
  reason: z.string().nullable().optional(),
});
export type DecideOutcome = z.infer<typeof DecideOutcome>;

export const DecideWording = z.object({
  decision: z.enum(["approved", "rejected", "withdrawn"]),
  reason: z.string(),
});
export type DecideWording = z.infer<typeof DecideWording>;

export const DecisionRequest = z.object({
  approve: z.boolean(),
  reason: z.string().nullable().optional(),
});
export type DecisionRequest = z.infer<typeof DecisionRequest>;

export const DeclineIn = z.object({
  reason: z.string(),
});
export type DeclineIn = z.infer<typeof DeclineIn>;

export const FillOut = z.object({
  amount: AmountOut,
  custodian_reference: z.string(),
  face: AmountOut,
  matures_on: z.string(),
  settled_on: z.string(),
});
export type FillOut = z.infer<typeof FillOut>;

export const OrderKind = z.enum(["auction_bid", "secondary_purchase", "secondary_sale"]);
export type OrderKind = z.infer<typeof OrderKind>;

export const OrderStatus = z.enum(["prepared", "approved", "sent_back", "withdrawn", "result_recorded", "settled", "unsuccessful"]);
export type OrderStatus = z.infer<typeof OrderStatus>;

export const OrderOut = z.object({
  allowed: z.array(Action),
  face: AmountOut,
  id: z.string(),
  kind: OrderKind,
  note: z.string().nullable(),
  prepared_at: z.string(),
  prepared_by: z.string(),
  product_code: z.string(),
  product_name: z.string(),
  result: FillOut.nullable(),
  result_recorded_by: z.string().nullable(),
  status: OrderStatus,
});
export type OrderOut = z.infer<typeof OrderOut>;

export const WithdrawalsOut = z.object({
  asked: AmountOut,
  clients: z.number().int(),
});
export type WithdrawalsOut = z.infer<typeof WithdrawalsOut>;

export const DealingApiPositionOut = z.object({
  bills_held: AmountOut,
  clients_owed: AmountOut,
  cover: Cover,
  discount_only: z.boolean(),
  face_held: AmountOut,
  lot_size: AmountOut,
  on_order: AmountOut,
  product_code: z.string(),
  product_name: z.string(),
  to_buy: AmountOut,
});
export type DealingApiPositionOut = z.infer<typeof DealingApiPositionOut>;

export const DeskOut = z.object({
  bills_held: AmountOut,
  buffer: BufferOut,
  early_withdrawals: WithdrawalsOut,
  matches_custodian: z.boolean().nullable(),
  money_to_place: AmountOut,
  orders: z.array(OrderOut),
  positions: z.array(DealingApiPositionOut),
});
export type DeskOut = z.infer<typeof DeskOut>;

export const DeviceSessionView = z.object({
  current: z.boolean(),
  device: z.string(),
  id: z.string(),
  last_seen_at: z.string(),
  second_step: z.enum(["passkey", "authenticator", "code", "signup"]),
  signed_in_at: z.string(),
  transport: z.enum(["cookie", "token"]),
});
export type DeviceSessionView = z.infer<typeof DeviceSessionView>;

export const DeviceSessionList = z.object({
  items: z.array(DeviceSessionView),
});
export type DeviceSessionList = z.infer<typeof DeviceSessionList>;

export const SignOffView = z.object({
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  figures: z.array(z.number().int()),
  id: z.string(),
  key: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  state: z.enum(["proposed", "approved", "rejected"]),
});
export type SignOffView = z.infer<typeof SignOffView>;

export const DifferenceView = z.object({
  currency: Currency.nullable(),
  figures: z.array(z.number().int()),
  key: z.string(),
  row: z.number().int().nullable(),
  sign_off: SignOffView.nullable(),
});
export type DifferenceView = z.infer<typeof DifferenceView>;

export const DocumentKind = z.enum(["certificate_of_incorporation", "board_resolution", "constitution", "group_resolution"]);
export type DocumentKind = z.infer<typeof DocumentKind>;

export const DocumentLink = z.object({
  content_type: z.string(),
  kind: DocumentKind,
  url: z.string(),
});
export type DocumentLink = z.infer<typeof DocumentLink>;

export const DocumentLinks = z.object({
  documents: z.array(DocumentLink),
  expires_at: z.string(),
});
export type DocumentLinks = z.infer<typeof DocumentLinks>;

export const DocumentNumber = z.object({
  document_number: z.string(),
});
export type DocumentNumber = z.infer<typeof DocumentNumber>;

export const DocumentOut = z.object({
  byte_size: z.number().int(),
  content_type: z.string(),
  kind: DocumentKind,
  uploaded_at: z.string(),
});
export type DocumentOut = z.infer<typeof DocumentOut>;

export const DocumentType = z.enum(["national_id", "passport", "driving_permit", "refugee_id"]);
export type DocumentType = z.infer<typeof DocumentType>;

export const Enrol = z.object({
  credential: z.record(z.string(), z.unknown()),
  passkey_name: z.string().optional(),
  password: z.string(),
  token: z.string(),
});
export type Enrol = z.infer<typeof Enrol>;

export const EnrolWithApp = z.object({
  code: z.string(),
  password: z.string(),
  token: z.string(),
});
export type EnrolWithApp = z.infer<typeof EnrolWithApp>;

export const EnrolmentToken = z.object({
  token: z.string(),
});
export type EnrolmentToken = z.infer<typeof EnrolmentToken>;

export const EnrolmentView = z.object({
  email: z.string(),
  methods: z.array(z.enum(["passkey", "authenticator", "code", "signup"])),
  name: z.string(),
  role: z.enum(["admin", "operations", "finance", "compliance", "support"]),
});
export type EnrolmentView = z.infer<typeof EnrolmentView>;

export const JsonValue = z.record(z.string(), z.unknown());
export type JsonValue = z.infer<typeof JsonValue>;

export const EventView = z.object({
  action: z.string(),
  actor_id: z.string().nullable(),
  actor_kind: z.enum(["client", "staff", "system", "provider", "anonymous"]),
  actor_name: z.string().nullable(),
  category: z.enum(["change", "access", "approval", "operator_control", "security"]),
  correlation_id: z.string().nullable(),
  details: z.record(z.string(), z.unknown()),
  id: z.string(),
  occurred_at: z.string(),
  outcome: z.enum(["succeeded", "denied", "failed"]),
  reason: z.string().nullable(),
  resource_id: z.string().nullable(),
  resource_type: z.string(),
});
export type EventView = z.infer<typeof EventView>;

export const EventPage = z.object({
  events: z.array(EventView),
  next_cursor: z.string().nullable(),
});
export type EventPage = z.infer<typeof EventPage>;

export const ExportOut = z.object({
  date_from: z.string(),
  date_to: z.string(),
  debit_total: AmountOut,
  exported_at: z.string(),
  exported_by: z.string(),
  exported_by_name: z.string().nullable(),
  id: z.string(),
  lines: z.number().int(),
});
export type ExportOut = z.infer<typeof ExportOut>;

export const Facets = z.object({
  actions: z.array(z.string()),
  resource_types: z.array(z.string()),
});
export type Facets = z.infer<typeof Facets>;

export const Figure = z.enum(["clients", "verified_identities", "running_investments", "pending_orders", "invested", "interest_before_tax"]);
export type Figure = z.infer<typeof Figure>;

export const FillIn = z.object({
  amount_minor: z.number().int(),
  custodian_reference: z.string(),
  face_minor: z.number().int(),
  matures_on: z.string(),
  settled_on: z.string(),
});
export type FillIn = z.infer<typeof FillIn>;

export const IncomeLineOut = z.object({
  amount: AmountOut,
  code: z.string(),
  name: z.string(),
});
export type IncomeLineOut = z.infer<typeof IncomeLineOut>;

export const IncomeOut = z.object({
  cost_total: AmountOut,
  costs: z.array(IncomeLineOut),
  income: z.array(IncomeLineOut),
  income_total: AmountOut,
  net: AmountOut,
});
export type IncomeOut = z.infer<typeof IncomeOut>;

export const FinanceScreenOut = z.object({
  accounts: z.array(BookLineOut),
  closed_through: z.string().nullable(),
  currency: Currency,
  date_from: z.string(),
  date_to: z.string(),
  exports: z.array(ExportOut),
  income: IncomeOut,
  month: z.string(),
  next_export: DaysOut.nullable(),
  sent_through: z.string().nullable(),
});
export type FinanceScreenOut = z.infer<typeof FinanceScreenOut>;

export const Frequency = z.enum(["weekly", "monthly"]);
export type Frequency = z.infer<typeof Frequency>;

export const GoalCategory = z.enum(["school_fees", "emergency", "land_or_home", "business", "wedding", "retirement", "other"]);
export type GoalCategory = z.infer<typeof GoalCategory>;

export const GoalStatus = z.enum(["active", "closed"]);
export type GoalStatus = z.infer<typeof GoalStatus>;

export const Standing = z.enum(["reached", "on_track", "behind", "no_date"]);
export type Standing = z.infer<typeof Standing>;

export const GroupProgressView = z.object({
  percent: z.number().int(),
  standing: Standing,
  value_minor: z.number().int(),
});
export type GroupProgressView = z.infer<typeof GroupProgressView>;

export const GroupStatus = z.enum(["active", "closed"]);
export type GroupStatus = z.infer<typeof GroupStatus>;

export const HeldAmountOut = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type HeldAmountOut = z.infer<typeof HeldAmountOut>;

export const HeldFor = z.enum(["size", "screening"]);
export type HeldFor = z.infer<typeof HeldFor>;

export const ReleaseOut = z.object({
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  payout_id: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  status: z.enum(["proposed", "approved", "declined", "withdrawn"]),
});
export type ReleaseOut = z.infer<typeof ReleaseOut>;

export const HeldPayoutOut = z.object({
  amount: MoneyView,
  business_date: z.string(),
  client_id: z.string(),
  created_at: z.string(),
  destination_id: z.string(),
  id: z.string(),
  kind: z.enum(["mobile_money", "bank"]),
  release: ReleaseOut.nullable(),
});
export type HeldPayoutOut = z.infer<typeof HeldPayoutOut>;

export const HeldUpPayoutOut = z.object({
  amount: MoneyView,
  client_id: z.string(),
  created_at: z.string(),
  first_tried_at: z.string(),
  id: z.string(),
  kind: z.literal("mobile_money"),
  last_tried_at: z.string(),
  tries: z.number().int(),
  waiting_for: z.literal("wallet_short").nullable().optional(),
});
export type HeldUpPayoutOut = z.infer<typeof HeldUpPayoutOut>;

export const InboxRow = z.object({
  client_id: z.string(),
  conversation: ConversationItem,
  last_author: z.enum(["client", "staff"]),
  last_message: z.string(),
  unread: z.number().int(),
});
export type InboxRow = z.infer<typeof InboxRow>;

export const Inbox = z.object({
  conversations: z.array(InboxRow),
});
export type Inbox = z.infer<typeof Inbox>;

export const InterestPaid = z.enum(["at_maturity", "monthly"]);
export type InterestPaid = z.infer<typeof InterestPaid>;

export const InterestRunOut = z.object({
  behind: z.number().int(),
  currency: Currency,
  finished_at: z.string().nullable(),
  gross_minor: z.number().int(),
  investments: z.number().int(),
  net_minor: z.number().int(),
  run_date: z.string(),
  started_at: z.string(),
  tax_minor: z.number().int(),
});
export type InterestRunOut = z.infer<typeof InterestRunOut>;

export const InvestmentBillsOut = z.object({
  bills: z.array(BillsOut),
  investment_id: z.string(),
  total: AmountOut.nullable(),
});
export type InvestmentBillsOut = z.infer<typeof InvestmentBillsOut>;

export const InvestmentStatus = z.enum(["awaiting_payment", "active", "cancelled", "matured", "withdrawn", "unwound", "transferred"]);
export type InvestmentStatus = z.infer<typeof InvestmentStatus>;

export const Kind = z.enum(["company", "group"]);
export type Kind = z.infer<typeof Kind>;

export const LiftView = z.object({
  address_hash: z.string(),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decided_by_name: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  proposed_by_name: z.string().nullable(),
  reason: z.string(),
  status: z.enum(["proposed", "approved", "rejected", "withdrawn"]),
  yours: z.boolean(),
});
export type LiftView = z.infer<typeof LiftView>;

export const SupportApiAmount = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type SupportApiAmount = z.infer<typeof SupportApiAmount>;

export const LogComplaint = z.object({
  amount: SupportApiAmount.nullable().optional(),
  category: z.enum(["payments", "withdrawals", "investments", "identity", "account", "fees", "service", "other"]),
  client_id: z.string(),
  client_key: z.string(),
  conversation_id: z.string().nullable().optional(),
  details: z.string(),
  owner_id: z.string().nullable().optional(),
  received_via: z.enum(["chat", "phone", "email", "in_person", "letter"]),
  summary: z.string(),
});
export type LogComplaint = z.infer<typeof LogComplaint>;

export const LoginCode = z.object({
  code: z.string(),
  login_id: z.string(),
});
export type LoginCode = z.infer<typeof LoginCode>;

export const LoginPasskey = z.object({
  credential: z.record(z.string(), z.unknown()),
  login_id: z.string(),
});
export type LoginPasskey = z.infer<typeof LoginPasskey>;

export const LoginPasskeyOptions = z.object({
  login_id: z.string(),
});
export type LoginPasskeyOptions = z.infer<typeof LoginPasskeyOptions>;

export const LoginStarted = z.object({
  login_id: z.string(),
  methods: z.array(z.enum(["passkey", "authenticator", "code", "signup"])),
});
export type LoginStarted = z.infer<typeof LoginStarted>;

export const Lookup = z.object({
  email: z.string(),
});
export type Lookup = z.infer<typeof Lookup>;

export const SuppressionView = z.object({
  address_hash: z.string(),
  complained: z.boolean(),
  created_at: z.string(),
  last_event_at: z.string().nullable(),
  lifted_at: z.string().nullable(),
  open_lift: LiftView.nullable(),
  reason: z.enum(["bounced", "complained"]),
  suppressed: z.boolean(),
});
export type SuppressionView = z.infer<typeof SuppressionView>;

export const LookupResult = z.object({
  suppression: SuppressionView.nullable(),
});
export type LookupResult = z.infer<typeof LookupResult>;

export const MaturityDayOut = z.object({
  currency: Currency,
  day: z.string(),
  held: z.number().int(),
  investments: z.number().int(),
  open_pay_out: z.number().int(),
  open_roll_over: z.number().int(),
  paid_out: z.number().int(),
  principal_minor: z.number().int(),
  rolled_over: z.number().int(),
});
export type MaturityDayOut = z.infer<typeof MaturityDayOut>;

export const StaffInvestmentOut = z.object({
  accrued_through: z.string().nullable(),
  amount_minor: z.number().int(),
  annual_rate_bps: z.number().int(),
  at_maturity: AtMaturity.nullable(),
  client_id: z.string(),
  created_at: z.string(),
  currency: Currency,
  id: z.string(),
  interest_gross_minor: z.number().int(),
  interest_net_minor: z.number().int(),
  maturity_date: z.string().nullable(),
  pay_by: z.string(),
  payout_id: z.string().nullable(),
  payout_refusal: z.string().nullable(),
  product_code: z.string(),
  product_name: z.string(),
  rolled_from_id: z.string().nullable(),
  rolled_into_id: z.string().nullable(),
  start_date: z.string().nullable(),
  status: InvestmentStatus,
  tax_bps: z.number().int(),
  tax_minor: z.number().int(),
  term_days: z.number().int().nullable(),
});
export type StaffInvestmentOut = z.infer<typeof StaffInvestmentOut>;

export const MaturitiesOut = z.object({
  days: z.array(MaturityDayOut),
  held: z.array(StaffInvestmentOut),
  not_closed: z.array(StaffInvestmentOut),
  today: z.string(),
});
export type MaturitiesOut = z.infer<typeof MaturitiesOut>;

export const MessageItem = z.object({
  author: z.enum(["client", "staff"]),
  author_name: z.string().nullable(),
  body: z.string(),
  conversation_id: z.string(),
  created_at: z.string(),
  id: z.string(),
});
export type MessageItem = z.infer<typeof MessageItem>;

export const MoneyIn = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type MoneyIn = z.infer<typeof MoneyIn>;

export const MonitoringRulesOut = z.object({
  currency: z.enum(["UGX", "USD"]),
  large_deposit_minor: z.number().int(),
  many_deposits_count: z.number().int(),
  many_deposits_hours: z.number().int(),
  quick_withdrawal_days: z.number().int(),
  quick_withdrawal_floor_minor: z.number().int(),
  quick_withdrawal_share_denominator: z.number().int(),
  quick_withdrawal_share_numerator: z.number().int(),
  rescreen_every_days: z.number().int(),
  split_deposits_days: z.number().int(),
  unusual_floor_minor: z.number().int(),
  unusual_history: z.number().int(),
  unusual_lookback_days: z.number().int(),
  unusual_multiple: z.number().int(),
});
export type MonitoringRulesOut = z.infer<typeof MonitoringRulesOut>;

export const MonthOut = z.object({
  due_on: z.string(),
  month: z.string(),
  outstanding: AmountOut,
  overdue: z.boolean(),
  remitted: AmountOut,
  waiting: AmountOut,
  withheld: AmountOut,
});
export type MonthOut = z.infer<typeof MonthOut>;

export const PayeeOut = z.object({
  account_number: z.string().nullable(),
  client_id: z.string(),
  gross: AmountOut,
  name: z.string().nullable(),
  tax: AmountOut,
  tin: z.string().nullable(),
});
export type PayeeOut = z.infer<typeof PayeeOut>;

export const MonthReportOut = z.object({
  currency: Currency,
  gross: AmountOut,
  month: z.string(),
  payees: z.array(PayeeOut),
  summary: MonthOut,
  tins_shown: z.boolean(),
  withheld: AmountOut,
});
export type MonthReportOut = z.infer<typeof MonthReportOut>;

export const MoveComplaint = z.object({
  note: z.string(),
  status: z.enum(["investigating", "waiting_for_client", "escalated"]),
});
export type MoveComplaint = z.infer<typeof MoveComplaint>;

export const MovementIn = z.object({
  amount_minor: z.number().int(),
  bank_reference: z.string().nullable().optional(),
  currency: Currency,
  direction: Direction,
  reason: z.string(),
  value_date: z.string().nullable().optional(),
});
export type MovementIn = z.infer<typeof MovementIn>;

export const Network = z.enum(["mtn", "airtel"]);
export type Network = z.infer<typeof Network>;

export const NewCheck = z.object({
  proof_name: z.string().nullable().optional(),
  reason: z.string(),
  statement_date: z.string().nullable().optional(),
});
export type NewCheck = z.infer<typeof NewCheck>;

export const NewOrderIn = z.object({
  currency: Currency,
  face_minor: z.number().int(),
  kind: OrderKind,
  product_code: z.string(),
});
export type NewOrderIn = z.infer<typeof NewOrderIn>;

export const NewSavedReply = z.object({
  body: z.string(),
  title: z.string(),
});
export type NewSavedReply = z.infer<typeof NewSavedReply>;

export const NotCarriedView = z.object({
  attempt: z.number().int(),
  code: z.string(),
  legacy_client_ref: z.string(),
  noted_at: z.string(),
  proposed_by: z.string().nullable(),
  reason: z.string().nullable(),
  resolution_id: z.string().nullable(),
  state: z.enum(["noted", "retry_proposed", "close_proposed", "retrying"]),
});
export type NotCarriedView = z.infer<typeof NotCarriedView>;

export const NoteBody = z.object({
  note: z.string(),
});
export type NoteBody = z.infer<typeof NoteBody>;

export const NoteIn = z.object({
  note: z.string(),
});
export type NoteIn = z.infer<typeof NoteIn>;

export const Verdict = z.enum(["pass", "fail", "unsure"]);
export type Verdict = z.infer<typeof Verdict>;

export const Outcome = z.object({
  check: AutomaticCheck,
  score: z.number().int().nullable(),
  verdict: Verdict,
});
export type Outcome = z.infer<typeof Outcome>;

export const SwitchView = z.object({
  as_of: z.string(),
  clients_carried: z.number().int(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  not_carried: z.array(NotCarriedView),
  proposed_at: z.string(),
  proposed_by: z.string(),
  state: z.enum(["proposed", "approved", "rejected"]),
  trial_id: z.string(),
});
export type SwitchView = z.infer<typeof SwitchView>;

export const TotalLineView = z.object({
  agrees: z.boolean(),
  currency: Currency.nullable(),
  figure: Figure,
  key: z.string(),
  new: z.number().int(),
  old: z.number().int(),
});
export type TotalLineView = z.infer<typeof TotalLineView>;

export const MigrationApiAmountView = z.object({
  currency: Currency,
  minor: z.number().int(),
});
export type MigrationApiAmountView = z.infer<typeof MigrationApiAmountView>;

export const TotalsView = z.object({
  as_of: z.string(),
  clients: z.number().int(),
  confirmed_by: z.string().nullable(),
  entered_at: z.string(),
  entered_by: z.string(),
  id: z.string(),
  interest_before_tax: z.array(MigrationApiAmountView),
  invested: z.array(MigrationApiAmountView),
  pending_orders: z.number().int(),
  running_investments: z.number().int(),
  verified_identities: z.number().int(),
});
export type TotalsView = z.infer<typeof TotalsView>;

export const Severity = z.enum(["error", "warning"]);
export type Severity = z.infer<typeof Severity>;

export const Sheet = z.enum(["clients", "investments"]);
export type Sheet = z.infer<typeof Sheet>;

export const ProblemView = z.object({
  code: z.string(),
  column: z.string().nullable(),
  row: z.number().int().nullable(),
  severity: Severity,
  sheet: Sheet,
});
export type ProblemView = z.infer<typeof ProblemView>;

export const TrialView = z.object({
  as_of: z.string(),
  error_count: z.number().int(),
  id: z.string(),
  problems: z.array(ProblemView),
  ran_at: z.string(),
  ran_by: z.string(),
  warning_count: z.number().int(),
});
export type TrialView = z.infer<typeof TrialView>;

export const OverviewView = z.object({
  allowed: z.array(z.enum(["run_trial", "enter_totals", "sign_off", "propose_switch", "decide_switch", "propose_resolution", "decide_resolution"])),
  blockers: z.array(z.enum(["no_trial", "import_has_errors", "totals_not_entered", "totals_not_confirmed", "difference_not_signed_off"])),
  claims: ClaimProgressView.nullable(),
  differences: z.array(DifferenceView),
  lines: z.array(TotalLineView),
  switch: SwitchView.nullable(),
  totals: TotalsView.nullable(),
  trial: TrialView.nullable(),
  trials_run: z.number().int(),
});
export type OverviewView = z.infer<typeof OverviewView>;

export const PartyOut = z.object({
  account_number: z.string().nullable(),
  client_id: z.string(),
  name: z.string().nullable(),
});
export type PartyOut = z.infer<typeof PartyOut>;

export const PasskeyOptions = z.object({
  public_key: z.record(z.string(), z.unknown()),
});
export type PasskeyOptions = z.infer<typeof PasskeyOptions>;

export const IdentityPeopleApiPersonView = z.object({
  id: z.string(),
  name: z.string(),
});
export type IdentityPeopleApiPersonView = z.infer<typeof IdentityPeopleApiPersonView>;

export const PeopleList = z.object({
  people: z.array(IdentityPeopleApiPersonView),
});
export type PeopleList = z.infer<typeof PeopleList>;

export const PhoneChangeView = z.object({
  client_id: z.string(),
  expires_at: z.string(),
  id: z.string(),
  new_phone: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
});
export type PhoneChangeView = z.infer<typeof PhoneChangeView>;

export const PhoneChangeList = z.object({
  items: z.array(PhoneChangeView),
});
export type PhoneChangeList = z.infer<typeof PhoneChangeList>;

export const PhotoLink = z.object({
  id: z.string(),
  kind: PhotoKind,
  url: z.string(),
});
export type PhotoLink = z.infer<typeof PhotoLink>;

export const PhotoLinks = z.object({
  expires_at: z.string(),
  photos: z.array(PhotoLink),
});
export type PhotoLinks = z.infer<typeof PhotoLinks>;

export const PlanCountsView = z.object({
  active: z.number().int(),
  paused: z.number().int(),
});
export type PlanCountsView = z.infer<typeof PlanCountsView>;

export const PotIn = z.object({
  account_reference: z.string(),
  source: z.enum(["iotec", "bank"]),
});
export type PotIn = z.infer<typeof PotIn>;

export const Problem = z.enum(["signatories_waiting", "too_few_signatories", "approvals_more_than_signatories", "approvals_too_few", "documents_missing", "registration_number_missing"]);
export type Problem = z.infer<typeof Problem>;

export const ProductKind = z.enum(["treasury_bill", "fixed_income"]);
export type ProductKind = z.infer<typeof ProductKind>;

export const ProductStatus = z.enum(["draft", "live", "paused"]);
export type ProductStatus = z.infer<typeof ProductStatus>;

export const ProgressView = z.object({
  awaiting_payment_minor: z.number().int(),
  interest_minor: z.number().int(),
  percent: z.number().int(),
  standing: Standing,
  value_minor: z.number().int(),
});
export type ProgressView = z.infer<typeof ProgressView>;

export const ProofLinkOut = z.object({
  expires_at: z.string(),
  url: z.string(),
});
export type ProofLinkOut = z.infer<typeof ProofLinkOut>;

export const ProposalIn = z.object({
  annual_rate_bps: z.number().int(),
  effective_from: z.string(),
  product_code: z.string(),
  reason: z.string(),
  tax_bps: z.number().int(),
});
export type ProposalIn = z.infer<typeof ProposalIn>;

export const RateOut = z.object({
  annual_rate_bps: z.number().int(),
  client_rate_percent: z.string(),
  effective_from: z.string(),
  tax_bps: z.number().int(),
});
export type RateOut = z.infer<typeof RateOut>;

export const ProposalOut = z.object({
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_note: z.string().nullable(),
  id: z.string(),
  product_code: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  rate: RateOut,
  reason: z.string(),
  status: ProposalStatus,
});
export type ProposalOut = z.infer<typeof ProposalOut>;

export const WordingText = z.object({
  body: z.string(),
  sms: z.string(),
  title: z.string(),
});
export type WordingText = z.infer<typeof WordingText>;

export const ProposalView = z.object({
  id: z.string(),
  preview: WordingText,
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  wording: WordingText,
  yours: z.boolean(),
});
export type ProposalView = z.infer<typeof ProposalView>;

export const ProposeAnnouncement = z.object({
  message: z.string(),
  reason: z.string(),
  title: z.string(),
});
export type ProposeAnnouncement = z.infer<typeof ProposeAnnouncement>;

export const ProposeBankAccount = z.object({
  account: BankAccountText,
  reason: z.string(),
});
export type ProposeBankAccount = z.infer<typeof ProposeBankAccount>;

export const ProposeChange = z.object({
  email: z.string().nullable().optional(),
  kind: z.enum(["invite", "change_role", "deactivate", "reactivate", "reset_sign_in"]),
  name: z.string().nullable().optional(),
  reason: z.string(),
  role: z.enum(["admin", "operations", "finance", "compliance", "support"]).nullable().optional(),
  staff_id: z.string().nullable().optional(),
});
export type ProposeChange = z.infer<typeof ProposeChange>;

export const ProposeIn = z.object({
  outcome: z.enum(["paid", "failed"]),
  reason: z.string(),
  statement_id: z.string().nullable().optional(),
  statement_line_id: z.string().nullable().optional(),
});
export type ProposeIn = z.infer<typeof ProposeIn>;

export const ProposeLift = z.object({
  reason: z.string(),
});
export type ProposeLift = z.infer<typeof ProposeLift>;

export const ProposeLock = z.object({
  kind: z.enum(["lock", "unlock"]),
  reason: z.string(),
});
export type ProposeLock = z.infer<typeof ProposeLock>;

export const ProposeOutcome = z.object({
  client_accepted: z.boolean(),
  outcome: z.enum(["upheld", "partly_upheld", "not_upheld"]),
  resolution: z.string(),
});
export type ProposeOutcome = z.infer<typeof ProposeOutcome>;

export const ProposePhone = z.object({
  phone: z.string(),
  reason: z.string(),
});
export type ProposePhone = z.infer<typeof ProposePhone>;

export const ProposeWording = z.object({
  reason: z.string(),
  wording: WordingText,
});
export type ProposeWording = z.infer<typeof ProposeWording>;

export const Proposed = z.object({
  id: z.string(),
});
export type Proposed = z.infer<typeof Proposed>;

export const QueueEntry = z.object({
  document_type: DocumentType,
  id: z.string(),
  name: z.string().nullable(),
  review_flags: z.array(z.string()),
  status: CheckStatus,
  submitted_at: z.string().nullable(),
});
export type QueueEntry = z.infer<typeof QueueEntry>;

export const Queue = z.object({
  items: z.array(QueueEntry),
});
export type Queue = z.infer<typeof Queue>;

export const ReasonBody = z.object({
  reason: z.string(),
});
export type ReasonBody = z.infer<typeof ReasonBody>;

export const ReasonIn = z.object({
  reason: z.string(),
});
export type ReasonIn = z.infer<typeof ReasonIn>;

export const ReassignmentOut = z.object({
  amount: MoneyView,
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_note: z.string().nullable(),
  deposit_id: z.string(),
  entry_id: z.string().nullable(),
  from_client_id: z.string(),
  from_investment_id: z.string(),
  id: z.string(),
  new_deposit_id: z.string().nullable(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  statement_line_id: z.string(),
  status: z.enum(["proposed", "applied", "sent_back", "withdrawn"]),
  to_client_id: z.string(),
  to_investment_id: z.string(),
});
export type ReassignmentOut = z.infer<typeof ReassignmentOut>;

export const ReassignmentDetailOut = z.object({
  can_decide: z.boolean(),
  from_client: PartyOut,
  line: StatementLineOut.nullable(),
  reassignment: ReassignmentOut,
  reference_names_from_client: z.boolean(),
  reference_names_to_client: z.boolean(),
  refusal: z.enum(["mobile_money_refund_instead", "not_confirmed", "not_for_an_investment", "not_matched_to_a_line", "money_moved_on", "same_client", "not_waiting_for_payment", "amount_differs_from_investment", "already_has_a_payment"]).nullable(),
  to_client: PartyOut,
  yours: z.boolean(),
});
export type ReassignmentDetailOut = z.infer<typeof ReassignmentDetailOut>;

export const ReassignmentProposeIn = z.object({
  deposit_id: z.string(),
  reason: z.string(),
  to_investment_id: z.string(),
});
export type ReassignmentProposeIn = z.infer<typeof ReassignmentProposeIn>;

export const ReassignmentSendBackIn = z.object({
  note: z.string(),
});
export type ReassignmentSendBackIn = z.infer<typeof ReassignmentSendBackIn>;

export const RecoveryClose = z.object({
  outcome: z.enum(["number_changed", "helped", "not_the_client"]),
});
export type RecoveryClose = z.infer<typeof RecoveryClose>;

export const RecoveryView = z.object({
  account_phone: z.string(),
  asks: z.number().int(),
  client_id: z.string(),
  contact_phone: z.string(),
  created_at: z.string(),
  id: z.string(),
  last_asked_at: z.string(),
  reason: z.enum(["lost_phone", "new_number", "no_codes", "other"]),
});
export type RecoveryView = z.infer<typeof RecoveryView>;

export const RecoveryList = z.object({
  items: z.array(RecoveryView),
});
export type RecoveryList = z.infer<typeof RecoveryList>;

export const Refusal = z.object({
  reason: z.string(),
  rejection_reason: z.enum(["name_different", "proof_unreadable", "proof_too_old", "proof_wrong_account", "proof_wrong_document", "suspected_fraud"]),
});
export type Refusal = z.infer<typeof Refusal>;

export const SupportApiComplaintSummary = z.object({
  average_days_to_close_tenths: z.number().int().nullable(),
  closed_this_quarter: z.number().int(),
  open: z.number().int(),
  overdue: z.number().int(),
  received_this_quarter: z.number().int(),
  target_working_days: z.number().int(),
  upheld_this_quarter: z.number().int(),
});
export type SupportApiComplaintSummary = z.infer<typeof SupportApiComplaintSummary>;

export const Register = z.object({
  complaints: z.array(StaffComplaint),
  summary: SupportApiComplaintSummary,
});
export type Register = z.infer<typeof Register>;

export const RejectBody = z.object({
  client_message: z.string().nullable().optional(),
  reason: z.string(),
});
export type RejectBody = z.infer<typeof RejectBody>;

export const Rejection = z.object({
  note: z.string(),
});
export type Rejection = z.infer<typeof Rejection>;

export const Relationship = z.enum(["spouse", "child", "parent", "sibling", "other_relative", "friend", "other"]);
export type Relationship = z.infer<typeof Relationship>;

export const RemittanceIn = z.object({
  amount_minor: z.number().int(),
  bank_reference: z.string(),
  currency: Currency,
  month: z.string(),
  note: z.string(),
  paid_on: z.string(),
  ura_reference: z.string(),
});
export type RemittanceIn = z.infer<typeof RemittanceIn>;

export const RemittanceStatus = z.enum(["proposed", "approved", "declined"]);
export type RemittanceStatus = z.infer<typeof RemittanceStatus>;

export const TaxApiStep = z.enum(["approve", "decline"]);
export type TaxApiStep = z.infer<typeof TaxApiStep>;

export const RemittanceOut = z.object({
  allowed: z.array(TaxApiStep),
  amount: AmountOut,
  bank_reference: z.string(),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decided_by_name: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  month: z.string(),
  note: z.string(),
  paid_on: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  proposed_by_name: z.string().nullable(),
  status: RemittanceStatus,
  ura_reference: z.string(),
});
export type RemittanceOut = z.infer<typeof RemittanceOut>;

export const Removal = z.object({
  reason: z.string(),
});
export type Removal = z.infer<typeof Removal>;

export const ResolutionOut = z.object({
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  outcome: z.enum(["paid", "failed"]),
  payout_id: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  statement_id: z.string().nullable(),
  statement_line_id: z.string().nullable(),
  status: z.enum(["proposed", "approved", "declined", "withdrawn"]),
});
export type ResolutionOut = z.infer<typeof ResolutionOut>;

export const ResolutionRequest = z.object({
  action: z.enum(["retry", "close"]),
  legacy_client_ref: z.string(),
  reason: z.string(),
});
export type ResolutionRequest = z.infer<typeof ResolutionRequest>;

export const ResolutionView = z.object({
  action: z.enum(["retry", "close"]),
  attempt: z.number().int(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  legacy_client_ref: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  state: z.enum(["proposed", "approved", "rejected"]),
  switch_id: z.string(),
});
export type ResolutionView = z.infer<typeof ResolutionView>;

export const ResultIn = z.object({
  fill: FillIn.nullable(),
});
export type ResultIn = z.infer<typeof ResultIn>;

export const ReturnIn = z.object({
  reason: z.string(),
  statement_line_id: z.string(),
});
export type ReturnIn = z.infer<typeof ReturnIn>;

export const ReturnLineOut = z.object({
  amount: MoneyView,
  id: z.string(),
  narrative: z.string(),
  reference: z.string(),
  value_date: z.string(),
});
export type ReturnLineOut = z.infer<typeof ReturnLineOut>;

export const ReturnOut = z.object({
  client_id: z.string(),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  ledger_entry_id: z.string().nullable(),
  payout_id: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  statement_line_id: z.string(),
  status: z.enum(["proposed", "approved", "declined", "withdrawn"]),
});
export type ReturnOut = z.infer<typeof ReturnOut>;

export const ReturnablePayoutOut = z.object({
  amount: MoneyView,
  bank_name: z.string().nullable(),
  client_id: z.string(),
  holder_name: z.string(),
  id: z.string(),
  kind: z.enum(["bank", "mobile_money"]),
  masked_account: z.string(),
  network: z.string().nullable(),
  paid_at: z.string(),
});
export type ReturnablePayoutOut = z.infer<typeof ReturnablePayoutOut>;

export const ReviewIn = z.object({
  reason: z.string(),
});
export type ReviewIn = z.infer<typeof ReviewIn>;

export const ReviewedPayoutOut = z.object({
  decision: z.enum(["approved", "declined"]),
  id: z.string(),
  status: z.enum(["queued", "sent", "uncertain", "paid", "failed", "cancelled"]),
});
export type ReviewedPayoutOut = z.infer<typeof ReviewedPayoutOut>;

export const RoleView = z.object({
  permissions: z.array(z.enum(["overview.view", "review_queue.view", "identity_checks.view", "identity_checks.review", "maturities.view", "support.view", "support.reply", "complaints.view", "complaints.manage", "complaints.close", "clients.view", "clients.lock", "clients.close", "money_movement.view", "reconciliation.view", "reconciliation.change", "investments.view", "automatic_investing.view", "goals.view", "dealing_desk.view", "dealing_orders.change", "finance.view", "tax_remittances.change", "products.view", "rates.view", "rates.change", "compliance.view", "compliance_cases.change", "team.view", "team.change", "reports.view", "audit.view", "settings.view", "wordings.change", "bank_accounts.change", "email_suppressions.lift", "announcements.send", "migration.view", "bank_transfers.change", "deposit_reassignments.change", "payout_accounts.change", "bank_payouts.change", "migration.import", "migration.sign_off", "client_phones.change", "investment_corrections.change", "investment_transfers.change", "organisations.review"])),
  role: z.enum(["admin", "operations", "finance", "compliance", "support"]),
});
export type RoleView = z.infer<typeof RoleView>;

export const RulesIn = z.object({
  currency: Currency,
  effective_from: z.string(),
  floor_minor: z.number().int(),
  from_buffer_up_to_minor: z.number().int(),
  pay_within_working_days: z.number().int(),
  reason: z.string(),
  target_minor: z.number().int(),
});
export type RulesIn = z.infer<typeof RulesIn>;

export const RunKind = z.enum(["interest", "maturity"]);
export type RunKind = z.infer<typeof RunKind>;

export const RunRequestIn = z.object({
  kind: RunKind,
  reason: z.string(),
  run_date: z.string(),
});
export type RunRequestIn = z.infer<typeof RunRequestIn>;

export const RunRequestOut = z.object({
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_note: z.string().nullable(),
  id: z.string(),
  job_id: z.string().nullable(),
  kind: RunKind,
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  run_date: z.string(),
  status: z.enum(["proposed", "approved", "declined", "withdrawn"]),
});
export type RunRequestOut = z.infer<typeof RunRequestOut>;

export const RunStatus = z.enum(["due", "requested", "paid", "failed", "missed"]);
export type RunStatus = z.infer<typeof RunStatus>;

export const SavedReplyItem = z.object({
  body: z.string(),
  id: z.string(),
  title: z.string(),
});
export type SavedReplyItem = z.infer<typeof SavedReplyItem>;

export const SavedReplies = z.object({
  replies: z.array(SavedReplyItem),
});
export type SavedReplies = z.infer<typeof SavedReplies>;

export const SearchResult = z.object({
  clients: z.array(AccountView),
});
export type SearchResult = z.infer<typeof SearchResult>;

export const SendBack = z.object({
  note: z.string(),
});
export type SendBack = z.infer<typeof SendBack>;

export const SendBackIn = z.object({
  note: z.string(),
});
export type SendBackIn = z.infer<typeof SendBackIn>;

export const SettlementOut = z.object({
  batch_id: z.string(),
  bulk_id: z.string().nullable().optional(),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decision_reason: z.string().nullable(),
  id: z.string(),
  outcome: z.enum(["paid", "failed"]),
  payout_id: z.string(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  reason: z.string(),
  statement_id: z.string().nullable(),
  statement_line_id: z.string().nullable(),
  status: z.enum(["proposed", "approved", "declined", "withdrawn"]),
});
export type SettlementOut = z.infer<typeof SettlementOut>;

export const SentBankPayoutOut = z.object({
  batch_id: z.string(),
  payout: BankPayoutOut,
  sent_at: z.string(),
  settlement: SettlementOut.nullable(),
});
export type SentBankPayoutOut = z.infer<typeof SentBankPayoutOut>;

export const SetTopic = z.object({
  topic: z.enum(["deposit", "withdrawal", "investment", "identity", "account", "other"]),
});
export type SetTopic = z.infer<typeof SetTopic>;

export const SettledPayoutOut = z.object({
  id: z.string(),
  status: z.enum(["paid", "failed"]),
});
export type SettledPayoutOut = z.infer<typeof SettledPayoutOut>;

export const SettlementIn = z.object({
  outcome: z.enum(["paid", "failed"]),
  reason: z.string(),
  statement_id: z.string().nullable().optional(),
  statement_line_id: z.string().nullable().optional(),
});
export type SettlementIn = z.infer<typeof SettlementIn>;

export const SignOffRequest = z.object({
  key: z.string(),
  reason: z.string(),
});
export type SignOffRequest = z.infer<typeof SignOffRequest>;

export const SignatoryStatus = z.enum(["invited", "joined", "declined", "removed"]);
export type SignatoryStatus = z.infer<typeof SignatoryStatus>;

export const SignedOut = z.object({
  ended: z.number().int(),
});
export type SignedOut = z.infer<typeof SignedOut>;

export const StaffChat = z.object({
  client_id: z.string(),
  conversation: ConversationItem,
  messages: z.array(MessageItem),
});
export type StaffChat = z.infer<typeof StaffChat>;

export const StaffCheck = z.object({
  account_number: z.string().nullable(),
  allowed: z.array(z.enum(["approve", "reject", "ask_for_photos", "escalate"])),
  automatic_checks: z.array(Outcome),
  carried_over: z.boolean(),
  client_id: z.string(),
  client_message: z.string().nullable(),
  date_of_birth: z.string().nullable(),
  decided_at: z.string().nullable(),
  document_country: z.string(),
  document_expires_on: z.string().nullable(),
  document_number_masked: z.string().nullable(),
  document_type: DocumentType,
  given_names: z.string().nullable(),
  id: z.string(),
  name: z.string().nullable(),
  photos: z.array(StaffPhoto),
  retake_photos: z.array(PhotoKind),
  review_flags: z.array(z.string()),
  status: CheckStatus,
  submitted_at: z.string().nullable(),
  surname: z.string().nullable(),
});
export type StaffCheck = z.infer<typeof StaffCheck>;

export const ClosuresApiAmountView = z.object({
  amount_minor: z.number().int(),
  currency: z.string(),
});
export type ClosuresApiAmountView = z.infer<typeof ClosuresApiAmountView>;

export const StandingView = z.object({
  blockers: z.array(Blocker),
  live_plans: z.number().int(),
  open_deposits: z.number().int(),
  open_goals: z.number().int(),
  open_payouts: z.number().int(),
  owed: z.array(ClosuresApiAmountView),
  running_investments: z.number().int(),
});
export type StandingView = z.infer<typeof StandingView>;

export const StaffClosureView = z.object({
  account_number: z.string().nullable(),
  can_approve: z.boolean(),
  can_decline: z.boolean(),
  can_prepare: z.boolean(),
  cancelled_at: z.string().nullable(),
  client_id: z.string(),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decided_by_name: z.string().nullable(),
  decision_note: z.string().nullable(),
  id: z.string(),
  prepared_at: z.string().nullable(),
  prepared_by: z.string().nullable(),
  prepared_by_name: z.string().nullable(),
  prepared_note: z.string().nullable(),
  reason: z.string().nullable(),
  requested_at: z.string(),
  standing: StandingView,
  status: z.enum(["requested", "prepared", "closed", "declined", "cancelled"]),
  verified_name: z.string().nullable(),
});
export type StaffClosureView = z.infer<typeof StaffClosureView>;

export const StaffClosureList = z.object({
  items: z.array(StaffClosureView),
});
export type StaffClosureList = z.infer<typeof StaffClosureList>;

export const StaffDestinationDetail = z.object({
  account: z.string(),
  account_number: z.string(),
  approved_at: z.string().nullable(),
  approved_by: z.string().nullable(),
  bank_code: z.string().nullable(),
  check: CheckOut.nullable(),
  client_id: z.string(),
  created_at: z.string(),
  holder_name: z.string(),
  id: z.string(),
  id_name: z.string().nullable(),
  kind: z.enum(["mobile_money", "bank"]),
  network: z.enum(["mtn", "airtel"]).nullable(),
  network_name: z.string().nullable(),
  payable_from: z.string().nullable(),
  proof_kind: z.enum(["bank_statement", "bank_letter", "bank_card", "network_registered_name"]).nullable(),
  proof_link: ProofLinkOut.nullable(),
  rejection_reason: z.union([z.enum(["name_different", "proof_unreadable", "proof_too_old", "proof_wrong_account", "proof_wrong_document", "suspected_fraud"]), z.literal("network_name_unavailable")]).nullable(),
  status: z.enum(["awaiting_code", "awaiting_proof", "in_review", "approved", "rejected", "removed"]),
});
export type StaffDestinationDetail = z.infer<typeof StaffDestinationDetail>;

export const StaffDestinationOut = z.object({
  account: z.string(),
  approved_at: z.string().nullable(),
  approved_by: z.string().nullable(),
  bank_code: z.string().nullable(),
  check: CheckOut.nullable(),
  client_id: z.string(),
  created_at: z.string(),
  holder_name: z.string(),
  id: z.string(),
  kind: z.enum(["mobile_money", "bank"]),
  network: z.enum(["mtn", "airtel"]).nullable(),
  network_name: z.string().nullable(),
  payable_from: z.string().nullable(),
  proof_kind: z.enum(["bank_statement", "bank_letter", "bank_card", "network_registered_name"]).nullable(),
  rejection_reason: z.union([z.enum(["name_different", "proof_unreadable", "proof_too_old", "proof_wrong_account", "proof_wrong_document", "suspected_fraud"]), z.literal("network_name_unavailable")]).nullable(),
  status: z.enum(["awaiting_code", "awaiting_proof", "in_review", "approved", "rejected", "removed"]),
});
export type StaffDestinationOut = z.infer<typeof StaffDestinationOut>;

export const StaffGoalView = z.object({
  category: GoalCategory,
  client_id: z.string(),
  closed_at: z.string().nullable(),
  created_at: z.string(),
  currency: Currency,
  goal_id: z.string(),
  name: z.string(),
  product_code: z.string(),
  status: GoalStatus,
  target_date: z.string().nullable(),
  target_minor: z.number().int(),
});
export type StaffGoalView = z.infer<typeof StaffGoalView>;

export const StaffSavingView = z.object({
  accrued_net_minor: z.number().int(),
  amount_minor: z.number().int(),
  investment_id: z.string(),
  maturity_date: z.string().nullable(),
  product_code: z.string(),
  start_date: z.string().nullable(),
  status: InvestmentStatus,
});
export type StaffSavingView = z.infer<typeof StaffSavingView>;

export const StaffGoalDetail = z.object({
  goal: StaffGoalView,
  progress: ProgressView,
  savings: z.array(StaffSavingView),
});
export type StaffGoalDetail = z.infer<typeof StaffGoalDetail>;

export const StaffGoalList = z.object({
  goals: z.array(StaffGoalView),
});
export type StaffGoalList = z.infer<typeof StaffGoalList>;

export const StaffGroupView = z.object({
  category: GoalCategory,
  closed_at: z.string().nullable(),
  created_at: z.string(),
  currency: Currency,
  group_id: z.string(),
  name: z.string(),
  owner_client_id: z.string(),
  status: GroupStatus,
  target_date: z.string().nullable(),
  target_minor: z.number().int(),
});
export type StaffGroupView = z.infer<typeof StaffGroupView>;

export const StaffMemberView = z.object({
  client_id: z.string(),
  display_name: z.string(),
  goal_id: z.string(),
  joined_at: z.string(),
  left_at: z.string().nullable(),
  value_minor: z.number().int(),
  value_on: z.string().nullable(),
});
export type StaffMemberView = z.infer<typeof StaffMemberView>;

export const StaffGroupDetail = z.object({
  group: StaffGroupView,
  members: z.array(StaffMemberView),
  progress: GroupProgressView,
});
export type StaffGroupDetail = z.infer<typeof StaffGroupDetail>;

export const StaffGroupList = z.object({
  groups: z.array(StaffGroupView),
});
export type StaffGroupList = z.infer<typeof StaffGroupList>;

export const StaffLoginStart = z.object({
  email: z.string(),
  password: z.string(),
});
export type StaffLoginStart = z.infer<typeof StaffLoginStart>;

export const StaffSignatoryOut = z.object({
  account_number: z.string(),
  answered_at: z.string().nullable(),
  client_id: z.string(),
  invited_at: z.string(),
  is_applicant: z.boolean(),
  name: z.string(),
  role: z.string(),
  status: SignatoryStatus,
  verified_name: z.string().nullable(),
});
export type StaffSignatoryOut = z.infer<typeof StaffSignatoryOut>;

export const Status = z.enum(["draft", "submitted", "checked", "approved", "rejected"]);
export type Status = z.infer<typeof Status>;

export const StaffOrganisationOut = z.object({
  account_number: z.string().nullable(),
  applicant_id: z.string(),
  approvals_needed: z.number().int(),
  can_approve: z.boolean(),
  can_check: z.boolean(),
  can_reject: z.boolean(),
  can_send_back: z.boolean(),
  check_note: z.string().nullable(),
  checked_at: z.string().nullable(),
  checked_by: z.string().nullable(),
  checked_by_name: z.string().nullable(),
  created_at: z.string(),
  decided_at: z.string().nullable(),
  decided_by: z.string().nullable(),
  decided_by_name: z.string().nullable(),
  decision_note: z.string().nullable(),
  documents: z.array(DocumentOut),
  documents_needed: z.array(DocumentKind),
  id: z.string(),
  kind: Kind,
  name: z.string(),
  problems: z.array(Problem),
  registration_number: z.string().nullable(),
  return_note: z.string().nullable(),
  returned_at: z.string().nullable(),
  signatories: z.array(StaffSignatoryOut),
  status: Status,
  submitted_at: z.string().nullable(),
});
export type StaffOrganisationOut = z.infer<typeof StaffOrganisationOut>;

export const StaffOrganisationList = z.object({
  items: z.array(StaffOrganisationOut),
});
export type StaffOrganisationList = z.infer<typeof StaffOrganisationList>;

export const StaffPersonView = z.object({
  full_name: z.string(),
  id_number_masked: z.string(),
  phone: z.string(),
  relationship: Relationship,
  share_bps: z.number().int(),
});
export type StaffPersonView = z.infer<typeof StaffPersonView>;

export const StaffProductOut = z.object({
  code: z.string(),
  currency: Currency,
  id: z.string(),
  interest_paid: InterestPaid,
  kind: ProductKind,
  minimum_minor: z.number().int(),
  name: z.string(),
  next_rate: RateOut.nullable(),
  rate: RateOut.nullable(),
  status: ProductStatus,
  term_days: z.number().int().nullable(),
});
export type StaffProductOut = z.infer<typeof StaffProductOut>;

export const StaffProfileView = z.object({
  next_of_kin: z.array(StaffPersonView).nullable(),
  next_of_kin_updated_at: z.string().nullable(),
  risk: z.array(AnswersView),
});
export type StaffProfileView = z.infer<typeof StaffProfileView>;

export const StaffRunView = z.object({
  amount_minor: z.number().int(),
  attempt: z.number().int(),
  client_id: z.string(),
  code: z.string().nullable(),
  currency: Currency,
  day: z.number().int(),
  due_on: z.string(),
  frequency: Frequency,
  investment_id: z.string().nullable(),
  network: Network,
  plan_id: z.string(),
  product_code: z.string(),
  product_name: z.string(),
  retry_on: z.string().nullable(),
  run_id: z.string(),
  status: RunStatus,
});
export type StaffRunView = z.infer<typeof StaffRunView>;

export const StaffRunList = z.object({
  next_before_id: z.string().nullable(),
  runs: z.array(StaffRunView),
});
export type StaffRunList = z.infer<typeof StaffRunList>;

export const StaffSendMessage = z.object({
  body: z.string(),
  key: z.string(),
});
export type StaffSendMessage = z.infer<typeof StaffSendMessage>;

export const StaffSessionView = z.object({
  confirmed_until: z.string(),
  name: z.string(),
  permissions: z.array(z.enum(["overview.view", "review_queue.view", "identity_checks.view", "identity_checks.review", "maturities.view", "support.view", "support.reply", "complaints.view", "complaints.manage", "complaints.close", "clients.view", "clients.lock", "clients.close", "money_movement.view", "reconciliation.view", "reconciliation.change", "investments.view", "automatic_investing.view", "goals.view", "dealing_desk.view", "dealing_orders.change", "finance.view", "tax_remittances.change", "products.view", "rates.view", "rates.change", "compliance.view", "compliance_cases.change", "team.view", "team.change", "reports.view", "audit.view", "settings.view", "wordings.change", "bank_accounts.change", "email_suppressions.lift", "announcements.send", "migration.view", "bank_transfers.change", "deposit_reassignments.change", "payout_accounts.change", "bank_payouts.change", "migration.import", "migration.sign_off", "client_phones.change", "investment_corrections.change", "investment_transfers.change", "organisations.review"])),
  role: z.enum(["admin", "operations", "finance", "compliance", "support"]),
  second_steps: z.array(z.enum(["passkey", "authenticator", "code", "signup"])),
  staff_id: z.string(),
});
export type StaffSessionView = z.infer<typeof StaffSessionView>;

export const TransferStatus = z.enum(["waiting", "held", "release_proposed", "done", "failed", "declined", "cancelled"]);
export type TransferStatus = z.infer<typeof TransferStatus>;

export const StaffTransferOut = z.object({
  amount_minor: z.number().int(),
  cancelled_at: z.string().nullable(),
  currency: Currency,
  decision_reason: z.string().nullable(),
  declined_at: z.string().nullable(),
  declined_by: z.string().nullable(),
  failure_code: z.string().nullable(),
  finished_at: z.string().nullable(),
  held: z.boolean(),
  held_for: HeldFor.nullable(),
  id: z.string(),
  investment_id: z.string(),
  kept_investment_id: z.string().nullable(),
  message: z.string().nullable(),
  received_investment_id: z.string().nullable(),
  recipient_client_id: z.string(),
  recipient_name: z.string(),
  release_proposed_at: z.string().nullable(),
  release_proposed_by: z.string().nullable(),
  release_reason: z.string().nullable(),
  released_at: z.string().nullable(),
  released_by: z.string().nullable(),
  requested_at: z.string(),
  sender_client_id: z.string(),
  sender_name: z.string(),
  sent_minor: z.number().int().nullable(),
  status: TransferStatus,
  whole: z.boolean(),
});
export type StaffTransferOut = z.infer<typeof StaffTransferOut>;

export const StaffWriteFirst = z.object({
  body: z.string(),
  key: z.string(),
  topic: z.enum(["deposit", "withdrawal", "investment", "identity", "account", "other"]).optional(),
});
export type StaffWriteFirst = z.infer<typeof StaffWriteFirst>;

export const StatementOut = z.object({
  account_reference: z.string(),
  business_date: z.string(),
  closing: MoneyOut,
  id: z.string(),
  line_count: z.number().int(),
  opening: MoneyOut,
  source: z.enum(["iotec", "bank"]),
});
export type StatementOut = z.infer<typeof StatementOut>;

export const StrandedPayoutOut = z.object({
  amount: MoneyView,
  client_id: z.string(),
  first_sent_at: z.string(),
  id: z.string(),
  resolution: ResolutionOut.nullable(),
  status: z.enum(["sent", "uncertain"]),
});
export type StrandedPayoutOut = z.infer<typeof StrandedPayoutOut>;

export const SummaryOut = z.object({
  last_screened_at: z.string().nullable(),
  not_run_last_30_days: z.number().int(),
  open_cases: z.number().int(),
  open_monitoring_cases: z.number().int(),
  open_screening_cases: z.number().int(),
  payouts_waiting: z.number().int(),
  pending: z.number().int(),
  provider: z.string(),
  reports_to_fia_this_year: z.number().int(),
  screened_last_30_days: z.number().int(),
  screening_on: z.boolean(),
  unavailable_last_30_days: z.number().int(),
  waiting_for_second_person: z.number().int(),
});
export type SummaryOut = z.infer<typeof SummaryOut>;

export const SuppressionList = z.object({
  can_lift: z.boolean(),
  suppressions: z.array(SuppressionView),
});
export type SuppressionList = z.infer<typeof SuppressionList>;

export const SwitchRequest = z.object({
  clients_csv: z.string(),
  investments_csv: z.string(),
});
export type SwitchRequest = z.infer<typeof SwitchRequest>;

export const TaxScreenOut = z.object({
  currency: Currency,
  held_for_ura: AmountOut,
  months: z.array(MonthOut),
  remittances: z.array(RemittanceOut),
});
export type TaxScreenOut = z.infer<typeof TaxScreenOut>;

export const TeamMemberView = z.object({
  created_at: z.string(),
  email: z.string(),
  has_authenticator: z.boolean(),
  has_passkey: z.boolean(),
  id: z.string(),
  last_signed_in_at: z.string().nullable(),
  name: z.string(),
  role: z.enum(["admin", "operations", "finance", "compliance", "support"]),
  status: z.enum(["invited", "active", "disabled"]),
});
export type TeamMemberView = z.infer<typeof TeamMemberView>;

export const TeamView = z.object({
  can_change: z.boolean(),
  changes: z.array(ChangeView),
  members: z.array(TeamMemberView),
  roles: z.array(RoleView),
});
export type TeamView = z.infer<typeof TeamView>;

export const TransferIn = z.object({
  from_pot: PotIn,
  provider_reference: z.string(),
  reason: z.string(),
  received: MoneyIn,
  sent: MoneyIn,
  to_pot: PotIn,
  value_date: z.string(),
});
export type TransferIn = z.infer<typeof TransferIn>;

export const ReconciliationTransfersApiPotOut = z.object({
  account_reference: z.string(),
  name: z.string(),
  source: z.enum(["iotec", "bank"]),
});
export type ReconciliationTransfersApiPotOut = z.infer<typeof ReconciliationTransfersApiPotOut>;

export const TransferOut = z.object({
  decided_by: z.string().nullable(),
  fee: MoneyOut,
  from_pot: ReconciliationTransfersApiPotOut,
  id: z.string(),
  ledger_entry_id: z.string().nullable(),
  proposed_at: z.string(),
  proposed_by: z.string(),
  provider_reference: z.string(),
  received: MoneyOut,
  sent: MoneyOut,
  status: z.enum(["proposed", "approved", "declined"]),
  to_pot: ReconciliationTransfersApiPotOut,
  value_date: z.string(),
});
export type TransferOut = z.infer<typeof TransferOut>;

export const TrialSummaryView = z.object({
  as_of: z.string(),
  clients_sha256: z.string(),
  error_count: z.number().int(),
  id: z.string(),
  investments_differing: z.number().int(),
  investments_sha256: z.string(),
  ran_at: z.string(),
  ran_by: z.string(),
  totals_confirmed: z.boolean(),
  totals_differing: z.number().int().nullable(),
  warning_count: z.number().int(),
});
export type TrialSummaryView = z.infer<typeof TrialSummaryView>;

export const TrialHistoryView = z.object({
  trials: z.array(TrialSummaryView),
});
export type TrialHistoryView = z.infer<typeof TrialHistoryView>;

export const TrialRequest = z.object({
  as_of: z.string(),
  clients_csv: z.string(),
  investments_csv: z.string(),
});
export type TrialRequest = z.infer<typeof TrialRequest>;

export const WaitingPayoutOut = z.object({
  amount: HeldAmountOut,
  client: ClientOut,
  created_at: z.string(),
  id: z.string(),
  kind: z.enum(["mobile_money", "bank"]),
  standing: StandingOut.nullable(),
});
export type WaitingPayoutOut = z.infer<typeof WaitingPayoutOut>;

export const WalletOut = z.object({
  connected: z.boolean(),
  difference: MoneyOut.nullable(),
  ledger_balance: MoneyOut,
  read_at: z.string().nullable(),
  wallet_balance: MoneyOut.nullable(),
});
export type WalletOut = z.infer<typeof WalletOut>;

export const WordingView = z.object({
  category: z.enum(["money", "investments", "earnings", "account", "security", "support", "news"]),
  changed_at: z.string().nullable(),
  edited: z.boolean(),
  event: z.string(),
  preview: WordingText,
  proposal: ProposalView.nullable(),
  variables: z.array(z.string()),
  wording: WordingText,
});
export type WordingView = z.infer<typeof WordingView>;

export const WordingList = z.object({
  wordings: z.array(WordingView),
});
export type WordingList = z.infer<typeof WordingList>;

export const ReconciliationApiMatchIn = z.object({
  entry_id: z.string(),
  line_no: z.number().int(),
  reason: z.string(),
  statement_line_id: z.string(),
});
export type ReconciliationApiMatchIn = z.infer<typeof ReconciliationApiMatchIn>;

export const ReconciliationApiPotOut = z.object({
  account_reference: z.string(),
  currency: z.enum(["UGX", "USD"]),
  last_closing: MoneyOut.nullable(),
  last_statement_date: z.string().nullable(),
  name: z.string(),
  source: z.enum(["iotec", "bank"]),
});
export type ReconciliationApiPotOut = z.infer<typeof ReconciliationApiPotOut>;

/** Every route, by method and path, with what it takes and what it answers. */
export const operations = {
  "GET /staff/announcements": {
    method: "GET",
    path: "/staff/announcements",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: AnnouncementList,
  },
  "POST /staff/announcements": {
    method: "POST",
    path: "/staff/announcements",
    params: z.object({}),
    query: z.object({}),
    body: ProposeAnnouncement,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: AnnouncementView,
  },
  "POST /staff/announcements/{announcement_id}/decision": {
    method: "POST",
    path: "/staff/announcements/{announcement_id}/decision",
    params: z.object({
    announcement_id: z.string(),
  }),
    query: z.object({}),
    body: DecideAnnouncement,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: AnnouncementView,
  },
  "GET /staff/approvals": {
    method: "GET",
    path: "/staff/approvals",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ApprovalsOut,
  },
  "GET /staff/audit/events": {
    method: "GET",
    path: "/staff/audit/events",
    params: z.object({}),
    query: z.object({
    cursor: z.string().nullable().optional(),
    limit: z.number().int().optional(),
    category: z.enum(["change", "access", "approval", "operator_control", "security"]).nullable().optional(),
    action: z.string().nullable().optional(),
    outcome: z.enum(["succeeded", "denied", "failed"]).nullable().optional(),
    actor_kind: z.enum(["client", "staff", "system", "provider", "anonymous"]).nullable().optional(),
    actor_id: z.string().nullable().optional(),
    resource_type: z.string().nullable().optional(),
    resource_id: z.string().nullable().optional(),
    correlation_id: z.string().nullable().optional(),
    since: z.string().nullable().optional(),
    until: z.string().nullable().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: EventPage,
  },
  "GET /staff/audit/events.csv": {
    method: "GET",
    path: "/staff/audit/events.csv",
    params: z.object({}),
    query: z.object({
    category: z.enum(["change", "access", "approval", "operator_control", "security"]).nullable().optional(),
    action: z.string().nullable().optional(),
    outcome: z.enum(["succeeded", "denied", "failed"]).nullable().optional(),
    actor_kind: z.enum(["client", "staff", "system", "provider", "anonymous"]).nullable().optional(),
    actor_id: z.string().nullable().optional(),
    resource_type: z.string().nullable().optional(),
    resource_id: z.string().nullable().optional(),
    correlation_id: z.string().nullable().optional(),
    since: z.string().nullable().optional(),
    until: z.string().nullable().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: null,
  },
  "GET /staff/audit/facets": {
    method: "GET",
    path: "/staff/audit/facets",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: Facets,
  },
  "GET /staff/autoinvest/plans/counts": {
    method: "GET",
    path: "/staff/autoinvest/plans/counts",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PlanCountsView,
  },
  "GET /staff/autoinvest/runs": {
    method: "GET",
    path: "/staff/autoinvest/runs",
    params: z.object({}),
    query: z.object({
    date_from: z.string(),
    date_to: z.string(),
    status: RunStatus.nullable().optional(),
    before_id: z.string().nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffRunList,
  },
  "GET /staff/claim-checks": {
    method: "GET",
    path: "/staff/claim-checks",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClaimQueue,
  },
  "GET /staff/claim-checks/{claim_check_id}": {
    method: "GET",
    path: "/staff/claim-checks/{claim_check_id}",
    params: z.object({
    claim_check_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffClaimCheck,
  },
  "POST /staff/claim-checks/{claim_check_id}/ask-for-photos": {
    method: "POST",
    path: "/staff/claim-checks/{claim_check_id}/ask-for-photos",
    params: z.object({
    claim_check_id: z.string(),
  }),
    query: z.object({}),
    body: AskForPhotosBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffClaimCheck,
  },
  "POST /staff/claim-checks/{claim_check_id}/match": {
    method: "POST",
    path: "/staff/claim-checks/{claim_check_id}/match",
    params: z.object({
    claim_check_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffClaimCheck,
  },
  "POST /staff/claim-checks/{claim_check_id}/no-match": {
    method: "POST",
    path: "/staff/claim-checks/{claim_check_id}/no-match",
    params: z.object({
    claim_check_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffClaimCheck,
  },
  "POST /staff/claim-checks/{claim_check_id}/photo-links": {
    method: "POST",
    path: "/staff/claim-checks/{claim_check_id}/photo-links",
    params: z.object({
    claim_check_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PhotoLinks,
  },
  "GET /staff/clients/cards": {
    method: "GET",
    path: "/staff/clients/cards",
    params: z.object({}),
    query: z.object({
    ids: z.array(z.string()),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientCards,
  },
  "POST /staff/clients/locks/{lock_id}/decision": {
    method: "POST",
    path: "/staff/clients/locks/{lock_id}/decision",
    params: z.object({
    lock_id: z.string(),
  }),
    query: z.object({}),
    body: DecideLock,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: LockView,
  },
  "GET /staff/clients/search": {
    method: "GET",
    path: "/staff/clients/search",
    params: z.object({}),
    query: z.object({
    q: z.string(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: SearchResult,
  },
  "GET /staff/clients/{client_id}": {
    method: "GET",
    path: "/staff/clients/{client_id}",
    params: z.object({
    client_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientPageView,
  },
  "POST /staff/clients/{client_id}/locks": {
    method: "POST",
    path: "/staff/clients/{client_id}/locks",
    params: z.object({
    client_id: z.string(),
  }),
    query: z.object({}),
    body: ProposeLock,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: LockView,
  },
  "POST /staff/clients/{client_id}/phone-changes": {
    method: "POST",
    path: "/staff/clients/{client_id}/phone-changes",
    params: z.object({
    client_id: z.string(),
  }),
    query: z.object({}),
    body: ProposePhone,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: Proposed,
  },
  "GET /staff/clients/{client_id}/profile": {
    method: "GET",
    path: "/staff/clients/{client_id}/profile",
    params: z.object({
    client_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffProfileView,
  },
  "GET /staff/closures": {
    method: "GET",
    path: "/staff/closures",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffClosureList,
  },
  "GET /staff/closures/{request_id}": {
    method: "GET",
    path: "/staff/closures/{request_id}",
    params: z.object({
    request_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffClosureView,
  },
  "POST /staff/closures/{request_id}/approval": {
    method: "POST",
    path: "/staff/closures/{request_id}/approval",
    params: z.object({
    request_id: z.string(),
  }),
    query: z.object({}),
    body: NoteBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffClosureView,
  },
  "POST /staff/closures/{request_id}/decline": {
    method: "POST",
    path: "/staff/closures/{request_id}/decline",
    params: z.object({
    request_id: z.string(),
  }),
    query: z.object({}),
    body: NoteBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffClosureView,
  },
  "POST /staff/closures/{request_id}/preparation": {
    method: "POST",
    path: "/staff/closures/{request_id}/preparation",
    params: z.object({
    request_id: z.string(),
  }),
    query: z.object({}),
    body: NoteBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffClosureView,
  },
  "GET /staff/complaints": {
    method: "GET",
    path: "/staff/complaints",
    params: z.object({}),
    query: z.object({
    show: z.enum(["open", "closed", "all"]).optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: Register,
  },
  "POST /staff/complaints": {
    method: "POST",
    path: "/staff/complaints",
    params: z.object({}),
    query: z.object({}),
    body: LogComplaint,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: StaffComplaint,
  },
  "GET /staff/complaints/{complaint_id}": {
    method: "GET",
    path: "/staff/complaints/{complaint_id}",
    params: z.object({
    complaint_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ComplaintDetail,
  },
  "POST /staff/complaints/{complaint_id}/notes": {
    method: "POST",
    path: "/staff/complaints/{complaint_id}/notes",
    params: z.object({
    complaint_id: z.string(),
  }),
    query: z.object({}),
    body: ComplaintNote,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffComplaint,
  },
  "POST /staff/complaints/{complaint_id}/outcome": {
    method: "POST",
    path: "/staff/complaints/{complaint_id}/outcome",
    params: z.object({
    complaint_id: z.string(),
  }),
    query: z.object({}),
    body: ProposeOutcome,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: PendingOutcome,
  },
  "POST /staff/complaints/{complaint_id}/outcome/decision": {
    method: "POST",
    path: "/staff/complaints/{complaint_id}/outcome/decision",
    params: z.object({
    complaint_id: z.string(),
  }),
    query: z.object({}),
    body: DecideOutcome,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffComplaint,
  },
  "PUT /staff/complaints/{complaint_id}/owner": {
    method: "PUT",
    path: "/staff/complaints/{complaint_id}/owner",
    params: z.object({
    complaint_id: z.string(),
  }),
    query: z.object({}),
    body: AssignComplaint,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffComplaint,
  },
  "POST /staff/complaints/{complaint_id}/status": {
    method: "POST",
    path: "/staff/complaints/{complaint_id}/status",
    params: z.object({
    complaint_id: z.string(),
  }),
    query: z.object({}),
    body: MoveComplaint,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffComplaint,
  },
  "GET /staff/compliance/cases": {
    method: "GET",
    path: "/staff/compliance/cases",
    params: z.object({}),
    query: z.object({
    status: z.enum(["open", "closed"]).nullable().optional(),
    source: z.enum(["screening", "monitoring"]).nullable().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(CaseOut),
  },
  "GET /staff/compliance/cases.csv": {
    method: "GET",
    path: "/staff/compliance/cases.csv",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: null,
  },
  "GET /staff/compliance/cases/{case_id}": {
    method: "GET",
    path: "/staff/compliance/cases/{case_id}",
    params: z.object({
    case_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: CaseDetailOut,
  },
  "POST /staff/compliance/cases/{case_id}/proposals": {
    method: "POST",
    path: "/staff/compliance/cases/{case_id}/proposals",
    params: z.object({
    case_id: z.string(),
  }),
    query: z.object({}),
    body: CaseProposeIn,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: CaseDetailOut,
  },
  "GET /staff/compliance/clients/{client_id}": {
    method: "GET",
    path: "/staff/compliance/clients/{client_id}",
    params: z.object({
    client_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientScreeningOut,
  },
  "POST /staff/compliance/clients/{client_id}/screenings": {
    method: "POST",
    path: "/staff/compliance/clients/{client_id}/screenings",
    params: z.object({
    client_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 201,
    response: RunOut,
  },
  "GET /staff/compliance/payouts": {
    method: "GET",
    path: "/staff/compliance/payouts",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(WaitingPayoutOut),
  },
  "POST /staff/compliance/proposals/{proposal_id}/approve": {
    method: "POST",
    path: "/staff/compliance/proposals/{proposal_id}/approve",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: ApproveIn.nullable(),
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: CaseDetailOut,
  },
  "POST /staff/compliance/proposals/{proposal_id}/send-back": {
    method: "POST",
    path: "/staff/compliance/proposals/{proposal_id}/send-back",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: SendBackIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: CaseDetailOut,
  },
  "POST /staff/compliance/proposals/{proposal_id}/withdraw": {
    method: "POST",
    path: "/staff/compliance/proposals/{proposal_id}/withdraw",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: CaseDetailOut,
  },
  "GET /staff/compliance/rules": {
    method: "GET",
    path: "/staff/compliance/rules",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: MonitoringRulesOut,
  },
  "GET /staff/compliance/summary": {
    method: "GET",
    path: "/staff/compliance/summary",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: SummaryOut,
  },
  "GET /staff/dealing/allocations": {
    method: "GET",
    path: "/staff/dealing/allocations",
    params: z.object({}),
    query: z.object({
    currency: Currency.optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: AllocationReportOut,
  },
  "GET /staff/dealing/allocations/investments/{investment_id}": {
    method: "GET",
    path: "/staff/dealing/allocations/investments/{investment_id}",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: InvestmentBillsOut,
  },
  "GET /staff/dealing/buffer": {
    method: "GET",
    path: "/staff/dealing/buffer",
    params: z.object({}),
    query: z.object({
    currency: Currency.optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: BufferScreenOut,
  },
  "POST /staff/dealing/buffer/capital": {
    method: "POST",
    path: "/staff/dealing/buffer/capital",
    params: z.object({}),
    query: z.object({}),
    body: MovementIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: BufferScreenOut,
  },
  "POST /staff/dealing/buffer/capital/{movement_id}/approve": {
    method: "POST",
    path: "/staff/dealing/buffer/capital/{movement_id}/approve",
    params: z.object({
    movement_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: BufferScreenOut,
  },
  "POST /staff/dealing/buffer/capital/{movement_id}/decline": {
    method: "POST",
    path: "/staff/dealing/buffer/capital/{movement_id}/decline",
    params: z.object({
    movement_id: z.string(),
  }),
    query: z.object({}),
    body: DeclineIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: BufferScreenOut,
  },
  "POST /staff/dealing/buffer/rules": {
    method: "POST",
    path: "/staff/dealing/buffer/rules",
    params: z.object({}),
    query: z.object({}),
    body: RulesIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: BufferScreenOut,
  },
  "POST /staff/dealing/buffer/rules/{proposal_id}/approve": {
    method: "POST",
    path: "/staff/dealing/buffer/rules/{proposal_id}/approve",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: BufferScreenOut,
  },
  "POST /staff/dealing/buffer/rules/{proposal_id}/send-back": {
    method: "POST",
    path: "/staff/dealing/buffer/rules/{proposal_id}/send-back",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: NoteIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: BufferScreenOut,
  },
  "POST /staff/dealing/buffer/rules/{proposal_id}/withdraw": {
    method: "POST",
    path: "/staff/dealing/buffer/rules/{proposal_id}/withdraw",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: BufferScreenOut,
  },
  "GET /staff/dealing/desk": {
    method: "GET",
    path: "/staff/dealing/desk",
    params: z.object({}),
    query: z.object({
    currency: Currency.optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: DeskOut,
  },
  "POST /staff/dealing/orders": {
    method: "POST",
    path: "/staff/dealing/orders",
    params: z.object({}),
    query: z.object({}),
    body: NewOrderIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: DeskOut,
  },
  "POST /staff/dealing/orders/{order_id}/approve": {
    method: "POST",
    path: "/staff/dealing/orders/{order_id}/approve",
    params: z.object({
    order_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: DeskOut,
  },
  "POST /staff/dealing/orders/{order_id}/confirm-result": {
    method: "POST",
    path: "/staff/dealing/orders/{order_id}/confirm-result",
    params: z.object({
    order_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: DeskOut,
  },
  "POST /staff/dealing/orders/{order_id}/reject-result": {
    method: "POST",
    path: "/staff/dealing/orders/{order_id}/reject-result",
    params: z.object({
    order_id: z.string(),
  }),
    query: z.object({}),
    body: NoteIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: DeskOut,
  },
  "POST /staff/dealing/orders/{order_id}/result": {
    method: "POST",
    path: "/staff/dealing/orders/{order_id}/result",
    params: z.object({
    order_id: z.string(),
  }),
    query: z.object({}),
    body: ResultIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: DeskOut,
  },
  "POST /staff/dealing/orders/{order_id}/send-back": {
    method: "POST",
    path: "/staff/dealing/orders/{order_id}/send-back",
    params: z.object({
    order_id: z.string(),
  }),
    query: z.object({}),
    body: NoteIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: DeskOut,
  },
  "POST /staff/dealing/orders/{order_id}/withdraw": {
    method: "POST",
    path: "/staff/dealing/orders/{order_id}/withdraw",
    params: z.object({
    order_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: DeskOut,
  },
  "GET /staff/email-suppressions": {
    method: "GET",
    path: "/staff/email-suppressions",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: SuppressionList,
  },
  "POST /staff/email-suppressions/lifts/{lift_id}/decision": {
    method: "POST",
    path: "/staff/email-suppressions/lifts/{lift_id}/decision",
    params: z.object({
    lift_id: z.string(),
  }),
    query: z.object({}),
    body: DecideLift,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: LiftView,
  },
  "POST /staff/email-suppressions/lookup": {
    method: "POST",
    path: "/staff/email-suppressions/lookup",
    params: z.object({}),
    query: z.object({}),
    body: Lookup,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: LookupResult,
  },
  "POST /staff/email-suppressions/{address_hash}/lifts": {
    method: "POST",
    path: "/staff/email-suppressions/{address_hash}/lifts",
    params: z.object({
    address_hash: z.string(),
  }),
    query: z.object({}),
    body: ProposeLift,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: LiftView,
  },
  "POST /staff/enrolment": {
    method: "POST",
    path: "/staff/enrolment",
    params: z.object({}),
    query: z.object({}),
    body: EnrolmentToken,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: EnrolmentView,
  },
  "POST /staff/enrolment/authenticator/options": {
    method: "POST",
    path: "/staff/enrolment/authenticator/options",
    params: z.object({}),
    query: z.object({}),
    body: EnrolmentToken,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: AuthenticatorSetupView,
  },
  "POST /staff/enrolment/authenticator/verify": {
    method: "POST",
    path: "/staff/enrolment/authenticator/verify",
    params: z.object({}),
    query: z.object({}),
    body: EnrolWithApp,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffSessionView,
  },
  "POST /staff/enrolment/passkey/options": {
    method: "POST",
    path: "/staff/enrolment/passkey/options",
    params: z.object({}),
    query: z.object({}),
    body: EnrolmentToken,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: PasskeyOptions,
  },
  "POST /staff/enrolment/passkey/verify": {
    method: "POST",
    path: "/staff/enrolment/passkey/verify",
    params: z.object({}),
    query: z.object({}),
    body: Enrol,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffSessionView,
  },
  "GET /staff/finance": {
    method: "GET",
    path: "/staff/finance",
    params: z.object({}),
    query: z.object({
    currency: Currency.optional(),
    month: z.string().nullable().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: FinanceScreenOut,
  },
  "GET /staff/finance/journal.csv": {
    method: "GET",
    path: "/staff/finance/journal.csv",
    params: z.object({}),
    query: z.object({
    date_from: z.string(),
    date_to: z.string(),
    currency: Currency.optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: null,
  },
  "GET /staff/goal-groups": {
    method: "GET",
    path: "/staff/goal-groups",
    params: z.object({}),
    query: z.object({
    status: GroupStatus.nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffGroupList,
  },
  "GET /staff/goal-groups/{group_id}": {
    method: "GET",
    path: "/staff/goal-groups/{group_id}",
    params: z.object({
    group_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffGroupDetail,
  },
  "GET /staff/goals": {
    method: "GET",
    path: "/staff/goals",
    params: z.object({}),
    query: z.object({
    client_id: z.string().nullable().optional(),
    status: GoalStatus.nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffGoalList,
  },
  "GET /staff/goals/{goal_id}": {
    method: "GET",
    path: "/staff/goals/{goal_id}",
    params: z.object({
    goal_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffGoalDetail,
  },
  "GET /staff/identity-checks": {
    method: "GET",
    path: "/staff/identity-checks",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: Queue,
  },
  "GET /staff/identity-checks/{check_id}": {
    method: "GET",
    path: "/staff/identity-checks/{check_id}",
    params: z.object({
    check_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffCheck,
  },
  "POST /staff/identity-checks/{check_id}/approve": {
    method: "POST",
    path: "/staff/identity-checks/{check_id}/approve",
    params: z.object({
    check_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffCheck,
  },
  "POST /staff/identity-checks/{check_id}/ask-for-photos": {
    method: "POST",
    path: "/staff/identity-checks/{check_id}/ask-for-photos",
    params: z.object({
    check_id: z.string(),
  }),
    query: z.object({}),
    body: AskForPhotosBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffCheck,
  },
  "POST /staff/identity-checks/{check_id}/document-number": {
    method: "POST",
    path: "/staff/identity-checks/{check_id}/document-number",
    params: z.object({
    check_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: DocumentNumber,
  },
  "POST /staff/identity-checks/{check_id}/escalate": {
    method: "POST",
    path: "/staff/identity-checks/{check_id}/escalate",
    params: z.object({
    check_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffCheck,
  },
  "POST /staff/identity-checks/{check_id}/photo-links": {
    method: "POST",
    path: "/staff/identity-checks/{check_id}/photo-links",
    params: z.object({
    check_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PhotoLinks,
  },
  "POST /staff/identity-checks/{check_id}/reject": {
    method: "POST",
    path: "/staff/identity-checks/{check_id}/reject",
    params: z.object({
    check_id: z.string(),
  }),
    query: z.object({}),
    body: RejectBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffCheck,
  },
  "GET /staff/interest-runs": {
    method: "GET",
    path: "/staff/interest-runs",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(InterestRunOut),
  },
  "GET /staff/investment-bookings": {
    method: "GET",
    path: "/staff/investment-bookings",
    params: z.object({}),
    query: z.object({
    status: z.enum(["proposed", "approved", "sent_back", "withdrawn", "done", "failed"]).nullable().optional(),
    client_id: z.string().nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(BookingOut),
  },
  "POST /staff/investment-bookings": {
    method: "POST",
    path: "/staff/investment-bookings",
    params: z.object({}),
    query: z.object({}),
    body: BookingIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: BookingOut,
  },
  "POST /staff/investment-bookings/{booking_id}/approve": {
    method: "POST",
    path: "/staff/investment-bookings/{booking_id}/approve",
    params: z.object({
    booking_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: BookingOut,
  },
  "POST /staff/investment-bookings/{booking_id}/send-back": {
    method: "POST",
    path: "/staff/investment-bookings/{booking_id}/send-back",
    params: z.object({
    booking_id: z.string(),
  }),
    query: z.object({}),
    body: SendBack,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: BookingOut,
  },
  "POST /staff/investment-bookings/{booking_id}/withdraw": {
    method: "POST",
    path: "/staff/investment-bookings/{booking_id}/withdraw",
    params: z.object({
    booking_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: BookingOut,
  },
  "GET /staff/investment-corrections": {
    method: "GET",
    path: "/staff/investment-corrections",
    params: z.object({}),
    query: z.object({
    status: CorrectionStatus.nullable().optional(),
    investment_id: z.string().nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(CorrectionOut),
  },
  "POST /staff/investment-corrections": {
    method: "POST",
    path: "/staff/investment-corrections",
    params: z.object({}),
    query: z.object({}),
    body: CorrectionIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: CorrectionOut,
  },
  "POST /staff/investment-corrections/{correction_id}/approve": {
    method: "POST",
    path: "/staff/investment-corrections/{correction_id}/approve",
    params: z.object({
    correction_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: CorrectionOut,
  },
  "POST /staff/investment-corrections/{correction_id}/send-back": {
    method: "POST",
    path: "/staff/investment-corrections/{correction_id}/send-back",
    params: z.object({
    correction_id: z.string(),
  }),
    query: z.object({}),
    body: SendBack,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: CorrectionOut,
  },
  "POST /staff/investment-corrections/{correction_id}/withdraw": {
    method: "POST",
    path: "/staff/investment-corrections/{correction_id}/withdraw",
    params: z.object({
    correction_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: CorrectionOut,
  },
  "GET /staff/investment-run-requests": {
    method: "GET",
    path: "/staff/investment-run-requests",
    params: z.object({}),
    query: z.object({
    status: z.enum(["proposed", "approved", "declined", "withdrawn"]).nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(RunRequestOut),
  },
  "POST /staff/investment-run-requests": {
    method: "POST",
    path: "/staff/investment-run-requests",
    params: z.object({}),
    query: z.object({}),
    body: RunRequestIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: RunRequestOut,
  },
  "POST /staff/investment-run-requests/{request_id}/approve": {
    method: "POST",
    path: "/staff/investment-run-requests/{request_id}/approve",
    params: z.object({
    request_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: RunRequestOut,
  },
  "POST /staff/investment-run-requests/{request_id}/decline": {
    method: "POST",
    path: "/staff/investment-run-requests/{request_id}/decline",
    params: z.object({
    request_id: z.string(),
  }),
    query: z.object({}),
    body: SendBack,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: RunRequestOut,
  },
  "POST /staff/investment-run-requests/{request_id}/withdraw": {
    method: "POST",
    path: "/staff/investment-run-requests/{request_id}/withdraw",
    params: z.object({
    request_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: RunRequestOut,
  },
  "GET /staff/investment-transfers": {
    method: "GET",
    path: "/staff/investment-transfers",
    params: z.object({}),
    query: z.object({
    status: TransferStatus.nullable().optional(),
    client_id: z.string().nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(StaffTransferOut),
  },
  "GET /staff/investment-transfers/held": {
    method: "GET",
    path: "/staff/investment-transfers/held",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(StaffTransferOut),
  },
  "POST /staff/investment-transfers/{transfer_id}/approve": {
    method: "POST",
    path: "/staff/investment-transfers/{transfer_id}/approve",
    params: z.object({
    transfer_id: z.string(),
  }),
    query: z.object({}),
    body: ReviewIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: StaffTransferOut,
  },
  "POST /staff/investment-transfers/{transfer_id}/decline": {
    method: "POST",
    path: "/staff/investment-transfers/{transfer_id}/decline",
    params: z.object({
    transfer_id: z.string(),
  }),
    query: z.object({}),
    body: ReviewIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: StaffTransferOut,
  },
  "POST /staff/investment-transfers/{transfer_id}/release": {
    method: "POST",
    path: "/staff/investment-transfers/{transfer_id}/release",
    params: z.object({
    transfer_id: z.string(),
  }),
    query: z.object({}),
    body: ReviewIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: StaffTransferOut,
  },
  "POST /staff/investment-transfers/{transfer_id}/withdraw": {
    method: "POST",
    path: "/staff/investment-transfers/{transfer_id}/withdraw",
    params: z.object({
    transfer_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffTransferOut,
  },
  "GET /staff/investments": {
    method: "GET",
    path: "/staff/investments",
    params: z.object({}),
    query: z.object({
    status: InvestmentStatus.nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(StaffInvestmentOut),
  },
  "GET /staff/investments/{investment_id}": {
    method: "GET",
    path: "/staff/investments/{investment_id}",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffInvestmentOut,
  },
  "POST /staff/login/authenticator/verify": {
    method: "POST",
    path: "/staff/login/authenticator/verify",
    params: z.object({}),
    query: z.object({}),
    body: LoginCode,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffSessionView,
  },
  "POST /staff/login/passkey/options": {
    method: "POST",
    path: "/staff/login/passkey/options",
    params: z.object({}),
    query: z.object({}),
    body: LoginPasskeyOptions,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: PasskeyOptions,
  },
  "POST /staff/login/passkey/verify": {
    method: "POST",
    path: "/staff/login/passkey/verify",
    params: z.object({}),
    query: z.object({}),
    body: LoginPasskey,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffSessionView,
  },
  "POST /staff/login/start": {
    method: "POST",
    path: "/staff/login/start",
    params: z.object({}),
    query: z.object({}),
    body: StaffLoginStart,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: LoginStarted,
  },
  "GET /staff/maturities": {
    method: "GET",
    path: "/staff/maturities",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: MaturitiesOut,
  },
  "GET /staff/migration": {
    method: "GET",
    path: "/staff/migration",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: OverviewView,
  },
  "PUT /staff/migration/control-totals": {
    method: "PUT",
    path: "/staff/migration/control-totals",
    params: z.object({}),
    query: z.object({}),
    body: ControlTotalsRequest,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: OverviewView,
  },
  "POST /staff/migration/control-totals/{totals_id}/confirmation": {
    method: "POST",
    path: "/staff/migration/control-totals/{totals_id}/confirmation",
    params: z.object({
    totals_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: OverviewView,
  },
  "POST /staff/migration/sign-offs": {
    method: "POST",
    path: "/staff/migration/sign-offs",
    params: z.object({}),
    query: z.object({}),
    body: SignOffRequest,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: SignOffView,
  },
  "POST /staff/migration/sign-offs/{sign_off_id}/decision": {
    method: "POST",
    path: "/staff/migration/sign-offs/{sign_off_id}/decision",
    params: z.object({
    sign_off_id: z.string(),
  }),
    query: z.object({}),
    body: DecisionRequest,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: SignOffView,
  },
  "POST /staff/migration/switch": {
    method: "POST",
    path: "/staff/migration/switch",
    params: z.object({}),
    query: z.object({}),
    body: SwitchRequest,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: SwitchView,
  },
  "POST /staff/migration/switch/resolutions/{resolution_id}/decision": {
    method: "POST",
    path: "/staff/migration/switch/resolutions/{resolution_id}/decision",
    params: z.object({
    resolution_id: z.string(),
  }),
    query: z.object({}),
    body: DecisionRequest,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ResolutionView,
  },
  "POST /staff/migration/switch/{switch_id}/decision": {
    method: "POST",
    path: "/staff/migration/switch/{switch_id}/decision",
    params: z.object({
    switch_id: z.string(),
  }),
    query: z.object({}),
    body: DecisionRequest,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: SwitchView,
  },
  "POST /staff/migration/switch/{switch_id}/resolutions": {
    method: "POST",
    path: "/staff/migration/switch/{switch_id}/resolutions",
    params: z.object({
    switch_id: z.string(),
  }),
    query: z.object({}),
    body: ResolutionRequest,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: ResolutionView,
  },
  "GET /staff/migration/trials": {
    method: "GET",
    path: "/staff/migration/trials",
    params: z.object({}),
    query: z.object({
    date_from: z.string(),
    date_to: z.string(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: TrialHistoryView,
  },
  "POST /staff/migration/trials": {
    method: "POST",
    path: "/staff/migration/trials",
    params: z.object({}),
    query: z.object({}),
    body: TrialRequest,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: OverviewView,
  },
  "GET /staff/notification-wordings": {
    method: "GET",
    path: "/staff/notification-wordings",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: WordingList,
  },
  "POST /staff/notification-wordings/proposals/{proposal_id}/decision": {
    method: "POST",
    path: "/staff/notification-wordings/proposals/{proposal_id}/decision",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: DecideWording,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ProposalView,
  },
  "POST /staff/notification-wordings/{event}/proposals": {
    method: "POST",
    path: "/staff/notification-wordings/{event}/proposals",
    params: z.object({
    event: z.string(),
  }),
    query: z.object({}),
    body: ProposeWording,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: ProposalView,
  },
  "GET /staff/organisations": {
    method: "GET",
    path: "/staff/organisations",
    params: z.object({}),
    query: z.object({
    status: Status.nullable().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffOrganisationList,
  },
  "GET /staff/organisations/{organisation_id}": {
    method: "GET",
    path: "/staff/organisations/{organisation_id}",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffOrganisationOut,
  },
  "POST /staff/organisations/{organisation_id}/approval": {
    method: "POST",
    path: "/staff/organisations/{organisation_id}/approval",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: NoteBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffOrganisationOut,
  },
  "POST /staff/organisations/{organisation_id}/check": {
    method: "POST",
    path: "/staff/organisations/{organisation_id}/check",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: NoteBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffOrganisationOut,
  },
  "POST /staff/organisations/{organisation_id}/document-links": {
    method: "POST",
    path: "/staff/organisations/{organisation_id}/document-links",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: DocumentLinks,
  },
  "POST /staff/organisations/{organisation_id}/rejection": {
    method: "POST",
    path: "/staff/organisations/{organisation_id}/rejection",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: NoteBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffOrganisationOut,
  },
  "POST /staff/organisations/{organisation_id}/return": {
    method: "POST",
    path: "/staff/organisations/{organisation_id}/return",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: NoteBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffOrganisationOut,
  },
  "GET /staff/payments/bank-accounts": {
    method: "GET",
    path: "/staff/payments/bank-accounts",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: BankAccountList,
  },
  "POST /staff/payments/bank-accounts/proposals/{proposal_id}/decision": {
    method: "POST",
    path: "/staff/payments/bank-accounts/proposals/{proposal_id}/decision",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: DecideBankAccount,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: BankAccountProposalView,
  },
  "POST /staff/payments/bank-accounts/{currency}/proposals": {
    method: "POST",
    path: "/staff/payments/bank-accounts/{currency}/proposals",
    params: z.object({
    currency: z.enum(["UGX", "USD"]),
  }),
    query: z.object({}),
    body: ProposeBankAccount,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: BankAccountProposalView,
  },
  "GET /staff/payments/bank-transfers": {
    method: "GET",
    path: "/staff/payments/bank-transfers",
    params: z.object({}),
    query: z.object({
    before: z.string().nullable().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: BankTransferPage,
  },
  "GET /staff/payments/bank-transfers/{payment_id}": {
    method: "GET",
    path: "/staff/payments/bank-transfers/{payment_id}",
    params: z.object({
    payment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: BankTransferOut,
  },
  "POST /staff/payments/bank-transfers/{payment_id}/approve": {
    method: "POST",
    path: "/staff/payments/bank-transfers/{payment_id}/approve",
    params: z.object({
    payment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: BankTransferOut,
  },
  "POST /staff/payments/bank-transfers/{payment_id}/review": {
    method: "POST",
    path: "/staff/payments/bank-transfers/{payment_id}/review",
    params: z.object({
    payment_id: z.string(),
  }),
    query: z.object({}),
    body: z.record(z.string(), z.unknown()),
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: BankTransferOut,
  },
  "GET /staff/payments/reassignments": {
    method: "GET",
    path: "/staff/payments/reassignments",
    params: z.object({}),
    query: z.object({
    status: z.enum(["proposed", "applied", "sent_back", "withdrawn"]).nullable().optional(),
    deposit_id: z.string().nullable().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(ReassignmentOut),
  },
  "POST /staff/payments/reassignments": {
    method: "POST",
    path: "/staff/payments/reassignments",
    params: z.object({}),
    query: z.object({}),
    body: ReassignmentProposeIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: ReassignmentDetailOut,
  },
  "GET /staff/payments/reassignments/{reassignment_id}": {
    method: "GET",
    path: "/staff/payments/reassignments/{reassignment_id}",
    params: z.object({
    reassignment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ReassignmentDetailOut,
  },
  "POST /staff/payments/reassignments/{reassignment_id}/approve": {
    method: "POST",
    path: "/staff/payments/reassignments/{reassignment_id}/approve",
    params: z.object({
    reassignment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: ReassignmentDetailOut,
  },
  "POST /staff/payments/reassignments/{reassignment_id}/send-back": {
    method: "POST",
    path: "/staff/payments/reassignments/{reassignment_id}/send-back",
    params: z.object({
    reassignment_id: z.string(),
  }),
    query: z.object({}),
    body: ReassignmentSendBackIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: ReassignmentDetailOut,
  },
  "POST /staff/payments/reassignments/{reassignment_id}/withdraw": {
    method: "POST",
    path: "/staff/payments/reassignments/{reassignment_id}/withdraw",
    params: z.object({
    reassignment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: ReassignmentDetailOut,
  },
  "GET /staff/payout-bank-batches": {
    method: "GET",
    path: "/staff/payout-bank-batches",
    params: z.object({}),
    query: z.object({
    status: z.array(z.enum(["prepared", "approved", "declined", "withdrawn"])).optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(BatchOut),
  },
  "POST /staff/payout-bank-batches": {
    method: "POST",
    path: "/staff/payout-bank-batches",
    params: z.object({}),
    query: z.object({}),
    body: BatchIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: BatchOut,
  },
  "GET /staff/payout-bank-batches/{batch_id}": {
    method: "GET",
    path: "/staff/payout-bank-batches/{batch_id}",
    params: z.object({
    batch_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: BatchOut,
  },
  "POST /staff/payout-bank-batches/{batch_id}/approve": {
    method: "POST",
    path: "/staff/payout-bank-batches/{batch_id}/approve",
    params: z.object({
    batch_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: BatchOut,
  },
  "GET /staff/payout-bank-batches/{batch_id}/bulk-debit": {
    method: "GET",
    path: "/staff/payout-bank-batches/{batch_id}/bulk-debit",
    params: z.object({
    batch_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: BulkDebitOut,
  },
  "POST /staff/payout-bank-batches/{batch_id}/bulk-settlements": {
    method: "POST",
    path: "/staff/payout-bank-batches/{batch_id}/bulk-settlements",
    params: z.object({
    batch_id: z.string(),
  }),
    query: z.object({}),
    body: BulkIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: BulkOut,
  },
  "POST /staff/payout-bank-batches/{batch_id}/decline": {
    method: "POST",
    path: "/staff/payout-bank-batches/{batch_id}/decline",
    params: z.object({
    batch_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: BatchOut,
  },
  "GET /staff/payout-bank-batches/{batch_id}/file": {
    method: "GET",
    path: "/staff/payout-bank-batches/{batch_id}/file",
    params: z.object({
    batch_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: null,
  },
  "POST /staff/payout-bank-batches/{batch_id}/withdraw": {
    method: "POST",
    path: "/staff/payout-bank-batches/{batch_id}/withdraw",
    params: z.object({
    batch_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: BatchOut,
  },
  "GET /staff/payout-bank-bulk-settlements": {
    method: "GET",
    path: "/staff/payout-bank-bulk-settlements",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(BulkOut),
  },
  "GET /staff/payout-bank-bulk-settlements/{bulk_id}": {
    method: "GET",
    path: "/staff/payout-bank-bulk-settlements/{bulk_id}",
    params: z.object({
    bulk_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: BulkOut,
  },
  "POST /staff/payout-bank-bulk-settlements/{bulk_id}/approve": {
    method: "POST",
    path: "/staff/payout-bank-bulk-settlements/{bulk_id}/approve",
    params: z.object({
    bulk_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: BulkOut,
  },
  "POST /staff/payout-bank-bulk-settlements/{bulk_id}/decline": {
    method: "POST",
    path: "/staff/payout-bank-bulk-settlements/{bulk_id}/decline",
    params: z.object({
    bulk_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: BulkOut,
  },
  "POST /staff/payout-bank-bulk-settlements/{bulk_id}/withdraw": {
    method: "POST",
    path: "/staff/payout-bank-bulk-settlements/{bulk_id}/withdraw",
    params: z.object({
    bulk_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: BulkOut,
  },
  "POST /staff/payout-bank-settlements/{settlement_id}/approve": {
    method: "POST",
    path: "/staff/payout-bank-settlements/{settlement_id}/approve",
    params: z.object({
    settlement_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: SettledPayoutOut,
  },
  "POST /staff/payout-bank-settlements/{settlement_id}/decline": {
    method: "POST",
    path: "/staff/payout-bank-settlements/{settlement_id}/decline",
    params: z.object({
    settlement_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: SettlementOut,
  },
  "POST /staff/payout-bank-settlements/{settlement_id}/withdraw": {
    method: "POST",
    path: "/staff/payout-bank-settlements/{settlement_id}/withdraw",
    params: z.object({
    settlement_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: SettlementOut,
  },
  "GET /staff/payout-destinations": {
    method: "GET",
    path: "/staff/payout-destinations",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(StaffDestinationOut),
  },
  "GET /staff/payout-destinations/{destination_id}": {
    method: "GET",
    path: "/staff/payout-destinations/{destination_id}",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffDestinationDetail,
  },
  "POST /staff/payout-destinations/{destination_id}/approve": {
    method: "POST",
    path: "/staff/payout-destinations/{destination_id}/approve",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: Approval,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffDestinationOut,
  },
  "POST /staff/payout-destinations/{destination_id}/check": {
    method: "POST",
    path: "/staff/payout-destinations/{destination_id}/check",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: NewCheck,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffDestinationOut,
  },
  "POST /staff/payout-destinations/{destination_id}/reject": {
    method: "POST",
    path: "/staff/payout-destinations/{destination_id}/reject",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: Refusal,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffDestinationOut,
  },
  "POST /staff/payout-destinations/{destination_id}/remove": {
    method: "POST",
    path: "/staff/payout-destinations/{destination_id}/remove",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: Removal,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: StaffDestinationOut,
  },
  "POST /staff/payout-releases/{release_id}/approve": {
    method: "POST",
    path: "/staff/payout-releases/{release_id}/approve",
    params: z.object({
    release_id: z.string(),
  }),
    query: z.object({}),
    body: ReviewIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: ReviewedPayoutOut,
  },
  "POST /staff/payout-releases/{release_id}/withdraw": {
    method: "POST",
    path: "/staff/payout-releases/{release_id}/withdraw",
    params: z.object({
    release_id: z.string(),
  }),
    query: z.object({}),
    body: ReviewIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ReleaseOut,
  },
  "POST /staff/payout-resolutions/{resolution_id}/approve": {
    method: "POST",
    path: "/staff/payout-resolutions/{resolution_id}/approve",
    params: z.object({
    resolution_id: z.string(),
  }),
    query: z.object({}),
    body: DecideIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: SettledPayoutOut,
  },
  "POST /staff/payout-resolutions/{resolution_id}/decline": {
    method: "POST",
    path: "/staff/payout-resolutions/{resolution_id}/decline",
    params: z.object({
    resolution_id: z.string(),
  }),
    query: z.object({}),
    body: DecideIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ResolutionOut,
  },
  "POST /staff/payout-resolutions/{resolution_id}/withdraw": {
    method: "POST",
    path: "/staff/payout-resolutions/{resolution_id}/withdraw",
    params: z.object({
    resolution_id: z.string(),
  }),
    query: z.object({}),
    body: DecideIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ResolutionOut,
  },
  "GET /staff/payout-returns": {
    method: "GET",
    path: "/staff/payout-returns",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(ReturnOut),
  },
  "GET /staff/payout-returns/returnable": {
    method: "GET",
    path: "/staff/payout-returns/returnable",
    params: z.object({}),
    query: z.object({
    amount_minor: z.number().int().nullable().optional(),
    currency: z.enum(["UGX", "USD"]).optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(ReturnablePayoutOut),
  },
  "GET /staff/payout-returns/{return_id}": {
    method: "GET",
    path: "/staff/payout-returns/{return_id}",
    params: z.object({
    return_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ReturnOut,
  },
  "POST /staff/payout-returns/{return_id}/approve": {
    method: "POST",
    path: "/staff/payout-returns/{return_id}/approve",
    params: z.object({
    return_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: ReturnOut,
  },
  "POST /staff/payout-returns/{return_id}/decline": {
    method: "POST",
    path: "/staff/payout-returns/{return_id}/decline",
    params: z.object({
    return_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ReturnOut,
  },
  "POST /staff/payout-returns/{return_id}/withdraw": {
    method: "POST",
    path: "/staff/payout-returns/{return_id}/withdraw",
    params: z.object({
    return_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ReturnOut,
  },
  "GET /staff/payouts/bank/sent": {
    method: "GET",
    path: "/staff/payouts/bank/sent",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(SentBankPayoutOut),
  },
  "GET /staff/payouts/bank/waiting": {
    method: "GET",
    path: "/staff/payouts/bank/waiting",
    params: z.object({}),
    query: z.object({
    currency: z.enum(["UGX", "USD"]).optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(BankPayoutOut),
  },
  "GET /staff/payouts/held": {
    method: "GET",
    path: "/staff/payouts/held",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(HeldPayoutOut),
  },
  "GET /staff/payouts/held-up": {
    method: "GET",
    path: "/staff/payouts/held-up",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(HeldUpPayoutOut),
  },
  "GET /staff/payouts/stranded": {
    method: "GET",
    path: "/staff/payouts/stranded",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(StrandedPayoutOut),
  },
  "POST /staff/payouts/{payout_id}/bank-settlements": {
    method: "POST",
    path: "/staff/payouts/{payout_id}/bank-settlements",
    params: z.object({
    payout_id: z.string(),
  }),
    query: z.object({}),
    body: SettlementIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: SettlementOut,
  },
  "POST /staff/payouts/{payout_id}/decline": {
    method: "POST",
    path: "/staff/payouts/{payout_id}/decline",
    params: z.object({
    payout_id: z.string(),
  }),
    query: z.object({}),
    body: ReviewIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: ReviewedPayoutOut,
  },
  "POST /staff/payouts/{payout_id}/release": {
    method: "POST",
    path: "/staff/payouts/{payout_id}/release",
    params: z.object({
    payout_id: z.string(),
  }),
    query: z.object({}),
    body: ReviewIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: ReleaseOut,
  },
  "POST /staff/payouts/{payout_id}/resolutions": {
    method: "POST",
    path: "/staff/payouts/{payout_id}/resolutions",
    params: z.object({
    payout_id: z.string(),
  }),
    query: z.object({}),
    body: ProposeIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: ResolutionOut,
  },
  "GET /staff/payouts/{payout_id}/return-lines": {
    method: "GET",
    path: "/staff/payouts/{payout_id}/return-lines",
    params: z.object({
    payout_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(ReturnLineOut),
  },
  "POST /staff/payouts/{payout_id}/returns": {
    method: "POST",
    path: "/staff/payouts/{payout_id}/returns",
    params: z.object({
    payout_id: z.string(),
  }),
    query: z.object({}),
    body: ReturnIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: ReturnOut,
  },
  "GET /staff/people": {
    method: "GET",
    path: "/staff/people",
    params: z.object({}),
    query: z.object({
    ids: z.array(z.string()).nullable().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PeopleList,
  },
  "GET /staff/phone-changes": {
    method: "GET",
    path: "/staff/phone-changes",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PhoneChangeList,
  },
  "POST /staff/phone-changes/{change_id}/approval": {
    method: "POST",
    path: "/staff/phone-changes/{change_id}/approval",
    params: z.object({
    change_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /staff/phone-changes/{change_id}/rejection": {
    method: "POST",
    path: "/staff/phone-changes/{change_id}/rejection",
    params: z.object({
    change_id: z.string(),
  }),
    query: z.object({}),
    body: Rejection,
    bodyKind: "json",
    idempotent: false,
    status: 204,
    response: null,
  },
  "GET /staff/products": {
    method: "GET",
    path: "/staff/products",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(StaffProductOut),
  },
  "GET /staff/products/{code}/rates": {
    method: "GET",
    path: "/staff/products/{code}/rates",
    params: z.object({
    code: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(RateOut),
  },
  "GET /staff/rate-proposals": {
    method: "GET",
    path: "/staff/rate-proposals",
    params: z.object({}),
    query: z.object({
    status: ProposalStatus.nullable().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(ProposalOut),
  },
  "POST /staff/rate-proposals": {
    method: "POST",
    path: "/staff/rate-proposals",
    params: z.object({}),
    query: z.object({}),
    body: ProposalIn,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: ProposalOut,
  },
  "POST /staff/rate-proposals/{proposal_id}/approve": {
    method: "POST",
    path: "/staff/rate-proposals/{proposal_id}/approve",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ProposalOut,
  },
  "POST /staff/rate-proposals/{proposal_id}/send-back": {
    method: "POST",
    path: "/staff/rate-proposals/{proposal_id}/send-back",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: SendBack,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ProposalOut,
  },
  "POST /staff/rate-proposals/{proposal_id}/withdraw": {
    method: "POST",
    path: "/staff/rate-proposals/{proposal_id}/withdraw",
    params: z.object({
    proposal_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ProposalOut,
  },
  "GET /staff/reconciliation/days/{day}": {
    method: "GET",
    path: "/staff/reconciliation/days/{day}",
    params: z.object({
    day: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: DayOut,
  },
  "POST /staff/reconciliation/days/{day}/approve": {
    method: "POST",
    path: "/staff/reconciliation/days/{day}/approve",
    params: z.object({
    day: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: CloseOut,
  },
  "POST /staff/reconciliation/days/{day}/prepare": {
    method: "POST",
    path: "/staff/reconciliation/days/{day}/prepare",
    params: z.object({
    day: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: CloseOut,
  },
  "POST /staff/reconciliation/days/{day}/refresh": {
    method: "POST",
    path: "/staff/reconciliation/days/{day}/refresh",
    params: z.object({
    day: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /staff/reconciliation/days/{day}/send-back": {
    method: "POST",
    path: "/staff/reconciliation/days/{day}/send-back",
    params: z.object({
    day: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: false,
    status: 204,
    response: null,
  },
  "GET /staff/reconciliation/iotec-wallet": {
    method: "GET",
    path: "/staff/reconciliation/iotec-wallet",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: WalletOut,
  },
  "POST /staff/reconciliation/matches": {
    method: "POST",
    path: "/staff/reconciliation/matches",
    params: z.object({}),
    query: z.object({}),
    body: ReconciliationApiMatchIn,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: z.record(z.string(), z.unknown()),
  },
  "GET /staff/reconciliation/pot-transfers": {
    method: "GET",
    path: "/staff/reconciliation/pot-transfers",
    params: z.object({}),
    query: z.object({
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(TransferOut),
  },
  "POST /staff/reconciliation/pot-transfers": {
    method: "POST",
    path: "/staff/reconciliation/pot-transfers",
    params: z.object({}),
    query: z.object({}),
    body: TransferIn,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: TransferOut,
  },
  "GET /staff/reconciliation/pot-transfers/{transfer_id}": {
    method: "GET",
    path: "/staff/reconciliation/pot-transfers/{transfer_id}",
    params: z.object({
    transfer_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: TransferOut,
  },
  "POST /staff/reconciliation/pot-transfers/{transfer_id}/approve": {
    method: "POST",
    path: "/staff/reconciliation/pot-transfers/{transfer_id}/approve",
    params: z.object({
    transfer_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: TransferOut,
  },
  "POST /staff/reconciliation/pot-transfers/{transfer_id}/decline": {
    method: "POST",
    path: "/staff/reconciliation/pot-transfers/{transfer_id}/decline",
    params: z.object({
    transfer_id: z.string(),
  }),
    query: z.object({}),
    body: ReasonIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: TransferOut,
  },
  "GET /staff/reconciliation/pots": {
    method: "GET",
    path: "/staff/reconciliation/pots",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(ReconciliationApiPotOut),
  },
  "GET /staff/reconciliation/statements": {
    method: "GET",
    path: "/staff/reconciliation/statements",
    params: z.object({}),
    query: z.object({
    source: z.enum(["iotec", "bank"]),
    account_reference: z.string(),
    date_from: z.string(),
    date_to: z.string(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(StatementOut),
  },
  "POST /staff/reconciliation/statements": {
    method: "POST",
    path: "/staff/reconciliation/statements",
    params: z.object({}),
    query: z.object({}),
    body: BodyImportStatementStaffReconciliationStatementsPost,
    bodyKind: "multipart",
    idempotent: false,
    status: 201,
    response: StatementOut,
  },
  "GET /staff/recovery-requests": {
    method: "GET",
    path: "/staff/recovery-requests",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: RecoveryList,
  },
  "POST /staff/recovery-requests/{request_id}/close": {
    method: "POST",
    path: "/staff/recovery-requests/{request_id}/close",
    params: z.object({
    request_id: z.string(),
  }),
    query: z.object({}),
    body: RecoveryClose,
    bodyKind: "json",
    idempotent: false,
    status: 204,
    response: null,
  },
  "GET /staff/session": {
    method: "GET",
    path: "/staff/session",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffSessionView,
  },
  "POST /staff/session/confirmation/authenticator": {
    method: "POST",
    path: "/staff/session/confirmation/authenticator",
    params: z.object({}),
    query: z.object({}),
    body: ConfirmCode,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: Confirmed,
  },
  "POST /staff/session/confirmation/passkey": {
    method: "POST",
    path: "/staff/session/confirmation/passkey",
    params: z.object({}),
    query: z.object({}),
    body: ConfirmWithPasskey,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: Confirmed,
  },
  "POST /staff/session/confirmation/passkey/options": {
    method: "POST",
    path: "/staff/session/confirmation/passkey/options",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PasskeyOptions,
  },
  "GET /staff/sessions": {
    method: "GET",
    path: "/staff/sessions",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: DeviceSessionList,
  },
  "POST /staff/sessions/sign-out-others": {
    method: "POST",
    path: "/staff/sessions/sign-out-others",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: SignedOut,
  },
  "DELETE /staff/sessions/{session_id}": {
    method: "DELETE",
    path: "/staff/sessions/{session_id}",
    params: z.object({
    session_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /staff/sign-out": {
    method: "POST",
    path: "/staff/sign-out",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /staff/support/clients/{client_id}/messages": {
    method: "POST",
    path: "/staff/support/clients/{client_id}/messages",
    params: z.object({
    client_id: z.string(),
  }),
    query: z.object({}),
    body: StaffWriteFirst,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: MessageItem,
  },
  "GET /staff/support/conversations": {
    method: "GET",
    path: "/staff/support/conversations",
    params: z.object({}),
    query: z.object({
    status: z.enum(["open", "solved"]).optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: Inbox,
  },
  "GET /staff/support/conversations/{conversation_id}": {
    method: "GET",
    path: "/staff/support/conversations/{conversation_id}",
    params: z.object({
    conversation_id: z.string(),
  }),
    query: z.object({
    after: z.string().nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: StaffChat,
  },
  "POST /staff/support/conversations/{conversation_id}/messages": {
    method: "POST",
    path: "/staff/support/conversations/{conversation_id}/messages",
    params: z.object({
    conversation_id: z.string(),
  }),
    query: z.object({}),
    body: StaffSendMessage,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: MessageItem,
  },
  "POST /staff/support/conversations/{conversation_id}/solve": {
    method: "POST",
    path: "/staff/support/conversations/{conversation_id}/solve",
    params: z.object({
    conversation_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ConversationItem,
  },
  "PUT /staff/support/conversations/{conversation_id}/topic": {
    method: "PUT",
    path: "/staff/support/conversations/{conversation_id}/topic",
    params: z.object({
    conversation_id: z.string(),
  }),
    query: z.object({}),
    body: SetTopic,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ConversationItem,
  },
  "GET /staff/support/saved-replies": {
    method: "GET",
    path: "/staff/support/saved-replies",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: SavedReplies,
  },
  "POST /staff/support/saved-replies": {
    method: "POST",
    path: "/staff/support/saved-replies",
    params: z.object({}),
    query: z.object({}),
    body: NewSavedReply,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: SavedReplyItem,
  },
  "DELETE /staff/support/saved-replies/{reply_id}": {
    method: "DELETE",
    path: "/staff/support/saved-replies/{reply_id}",
    params: z.object({
    reply_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "GET /staff/tax": {
    method: "GET",
    path: "/staff/tax",
    params: z.object({}),
    query: z.object({
    currency: Currency.optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: TaxScreenOut,
  },
  "GET /staff/tax/months/{month}": {
    method: "GET",
    path: "/staff/tax/months/{month}",
    params: z.object({
    month: z.string(),
  }),
    query: z.object({
    currency: Currency.optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: MonthReportOut,
  },
  "GET /staff/tax/months/{month}/payees.csv": {
    method: "GET",
    path: "/staff/tax/months/{month}/payees.csv",
    params: z.object({
    month: z.string(),
  }),
    query: z.object({
    currency: Currency.optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: null,
  },
  "POST /staff/tax/remittances": {
    method: "POST",
    path: "/staff/tax/remittances",
    params: z.object({}),
    query: z.object({}),
    body: RemittanceIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: TaxScreenOut,
  },
  "POST /staff/tax/remittances/{remittance_id}/approve": {
    method: "POST",
    path: "/staff/tax/remittances/{remittance_id}/approve",
    params: z.object({
    remittance_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: true,
    status: 200,
    response: TaxScreenOut,
  },
  "POST /staff/tax/remittances/{remittance_id}/decline": {
    method: "POST",
    path: "/staff/tax/remittances/{remittance_id}/decline",
    params: z.object({
    remittance_id: z.string(),
  }),
    query: z.object({}),
    body: DeclineIn,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: TaxScreenOut,
  },
  "GET /staff/team": {
    method: "GET",
    path: "/staff/team",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: TeamView,
  },
  "POST /staff/team/changes": {
    method: "POST",
    path: "/staff/team/changes",
    params: z.object({}),
    query: z.object({}),
    body: ProposeChange,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: ChangeView,
  },
  "POST /staff/team/changes/{change_id}/decision": {
    method: "POST",
    path: "/staff/team/changes/{change_id}/decision",
    params: z.object({
    change_id: z.string(),
  }),
    query: z.object({}),
    body: DecideChange,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ChangeView,
  },
} as const;

export type Operations = typeof operations;
export type OperationName = keyof Operations;
