// Generated from openapi.json by scripts/generate.ts. Do not edit.
/* eslint-disable */
import { z } from "zod";

/** A file for a multipart upload: a Blob on the web, a local file description on a phone. */
export type Upload = Blob | { uri: string; name: string; type: string };
export const Upload = z.custom<Upload>((value) => value !== null && typeof value === "object");

export const AnswerBody = z.object({
  join: z.boolean(),
});
export type AnswerBody = z.infer<typeof AnswerBody>;

export const AnswersBody = z.object({
  answers: z.record(z.string(), z.unknown()),
});
export type AnswersBody = z.infer<typeof AnswersBody>;

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

export const Kind = z.enum(["company", "group"]);
export type Kind = z.infer<typeof Kind>;

export const ApplyBody = z.object({
  approvals_needed: z.number().int(),
  kind: Kind,
  name: z.string(),
  registration_number: z.string().nullable().optional(),
  role: z.string(),
});
export type ApplyBody = z.infer<typeof ApplyBody>;

export const AskBody = z.object({
  reason: z.string().nullable().optional(),
});
export type AskBody = z.infer<typeof AskBody>;

export const AtMaturity = z.enum(["roll_over", "pay_out"]);
export type AtMaturity = z.infer<typeof AtMaturity>;

export const AuthenticatorSetupView = z.object({
  secret: z.string(),
  uri: z.string(),
});
export type AuthenticatorSetupView = z.infer<typeof AuthenticatorSetupView>;

export const AuthenticatorState = z.object({
  enabled: z.boolean(),
});
export type AuthenticatorState = z.infer<typeof AuthenticatorState>;

export const BankOut = z.object({
  code: z.string(),
  name: z.string(),
});
export type BankOut = z.infer<typeof BankOut>;

export const MoneyView = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type MoneyView = z.infer<typeof MoneyView>;

export const BankTransferDetails = z.object({
  account_name: z.string(),
  account_number: z.string(),
  amount: MoneyView,
  bank_name: z.string(),
  branch: z.string().nullable(),
  reference: z.string(),
});
export type BankTransferDetails = z.infer<typeof BankTransferDetails>;

export const Blocker = z.enum(["investments_running", "money_owed", "deposits_in_progress", "payouts_in_progress", "goals_open", "plans_live"]);
export type Blocker = z.infer<typeof Blocker>;

export const BodyAddDocumentClientOrganisationsOrganisationIdDocumentsKindPut = z.object({
  document: Upload,
});
export type BodyAddDocumentClientOrganisationsOrganisationIdDocumentsKindPut = z.infer<typeof BodyAddDocumentClientOrganisationsOrganisationIdDocumentsKindPut>;

export const BodyAddPhotoClientClaimCheckPhotosKindPut = z.object({
  photo: Upload,
});
export type BodyAddPhotoClientClaimCheckPhotosKindPut = z.infer<typeof BodyAddPhotoClientClaimCheckPhotosKindPut>;

export const BodyAddPhotoClientIdentityCheckPhotosKindPut = z.object({
  photo: Upload,
});
export type BodyAddPhotoClientIdentityCheckPhotosKindPut = z.infer<typeof BodyAddPhotoClientIdentityCheckPhotosKindPut>;

export const BodyPayByBankTransferClientInvestmentsInvestmentIdPaymentsBankTransferPost = z.object({
  amount_minor: z.number().int(),
  bank_name: z.string(),
  receipt: Upload,
  sent_on: z.string(),
  transaction_number: z.string(),
});
export type BodyPayByBankTransferClientInvestmentsInvestmentIdPaymentsBankTransferPost = z.infer<typeof BodyPayByBankTransferClientInvestmentsInvestmentIdPaymentsBankTransferPost>;

export const BodyUploadProofClientPayoutDestinationsDestinationIdProofPost = z.object({
  file: Upload,
  proof_kind: z.enum(["bank_statement", "bank_letter", "bank_card"]),
});
export type BodyUploadProofClientPayoutDestinationsDestinationIdProofPost = z.infer<typeof BodyUploadProofClientPayoutDestinationsDestinationIdProofPost>;

export const CategoryChannels = z.object({
  always_on: z.boolean(),
  category: z.enum(["money", "investments", "earnings", "account", "security", "support", "news"]),
  channels: z.array(z.enum(["push", "sms", "email"])),
});
export type CategoryChannels = z.infer<typeof CategoryChannels>;

export const ChangeBody = z.object({
  approvals_needed: z.number().int().nullable().optional(),
  name: z.string().nullable().optional(),
  registration_number: z.string().nullable().optional(),
});
export type ChangeBody = z.infer<typeof ChangeBody>;

export const CheckStatus = z.enum(["draft", "submitted", "needs_review", "with_compliance", "awaiting_client", "approved", "rejected"]);
export type CheckStatus = z.infer<typeof CheckStatus>;

export const ClaimCheckStatus = z.enum(["draft", "needs_review", "awaiting_client", "matched", "not_matched"]);
export type ClaimCheckStatus = z.infer<typeof ClaimCheckStatus>;

export const ClaimComplete = z.object({
  challenge_id: z.string(),
  code: z.string(),
  password: z.string(),
  token: z.string(),
});
export type ClaimComplete = z.infer<typeof ClaimComplete>;

export const ClaimStart = z.object({
  token: z.string(),
});
export type ClaimStart = z.infer<typeof ClaimStart>;

export const ClaimStarted = z.object({
  challenge_id: z.string(),
});
export type ClaimStarted = z.infer<typeof ClaimStarted>;

export const ClaimStatus = z.enum(["not_invited", "invited", "signed_in", "identity_submitted", "identity_confirmed", "confirmed", "problem_reported"]);
export type ClaimStatus = z.infer<typeof ClaimStatus>;

export const IdentityStep = z.enum(["selfie", "full_id_check"]);
export type IdentityStep = z.infer<typeof IdentityStep>;

export const ClaimStateView = z.object({
  identity_step: IdentityStep,
  problem_reported: z.boolean(),
  status: ClaimStatus,
});
export type ClaimStateView = z.infer<typeof ClaimStateView>;

export const ConversationItem = z.object({
  created_at: z.string(),
  id: z.string(),
  last_message_at: z.string(),
  status: z.enum(["open", "solved"]),
  topic: z.enum(["deposit", "withdrawal", "investment", "identity", "account", "other"]),
  waiting_since: z.string().nullable(),
});
export type ConversationItem = z.infer<typeof ConversationItem>;

export const MessageItem = z.object({
  author: z.enum(["client", "staff"]),
  author_name: z.string().nullable(),
  body: z.string(),
  conversation_id: z.string(),
  created_at: z.string(),
  id: z.string(),
});
export type MessageItem = z.infer<typeof MessageItem>;

export const ClientChat = z.object({
  conversation: ConversationItem.nullable(),
  messages: z.array(MessageItem),
  unread: z.number().int(),
});
export type ClientChat = z.infer<typeof ClientChat>;

export const ClientDetails = z.object({
  date_of_birth: z.string(),
  document_expires_on: z.string().nullable(),
  document_number: z.string(),
  given_names: z.string(),
  surname: z.string(),
  tin: z.string().nullable(),
});
export type ClientDetails = z.infer<typeof ClientDetails>;

export const PhotoKind = z.enum(["front", "back", "selfie"]);
export type PhotoKind = z.infer<typeof PhotoKind>;

export const ClientPhoto = z.object({
  byte_size: z.number().int(),
  content_type: z.string(),
  kind: PhotoKind,
  live: z.boolean().optional(),
});
export type ClientPhoto = z.infer<typeof ClientPhoto>;

export const DocumentType = z.enum(["national_id", "passport", "driving_permit", "refugee_id"]);
export type DocumentType = z.infer<typeof DocumentType>;

export const ClientCheck = z.object({
  account_number: z.string().nullable(),
  client_message: z.string().nullable(),
  details: ClientDetails.nullable(),
  details_confirmed: z.boolean(),
  document_country: z.string(),
  document_type: DocumentType,
  id: z.string(),
  missing: z.array(z.string()),
  photos: z.array(ClientPhoto),
  retake_photos: z.array(PhotoKind),
  retaken: z.array(PhotoKind),
  status: CheckStatus,
  submitted_at: z.string().nullable(),
});
export type ClientCheck = z.infer<typeof ClientCheck>;

export const ClientCheckEnvelope = z.object({
  check: ClientCheck.nullable(),
});
export type ClientCheckEnvelope = z.infer<typeof ClientCheckEnvelope>;

export const ClientClaimCheck = z.object({
  client_message: z.string().nullable(),
  id: z.string(),
  missing: z.array(z.string()),
  photos: z.array(ClientPhoto),
  retake_photos: z.array(PhotoKind),
  retaken: z.array(PhotoKind),
  status: ClaimCheckStatus,
  submitted_at: z.string().nullable(),
});
export type ClientClaimCheck = z.infer<typeof ClientClaimCheck>;

export const ClientClaimCheckEnvelope = z.object({
  check: ClientClaimCheck.nullable(),
});
export type ClientClaimCheckEnvelope = z.infer<typeof ClientClaimCheckEnvelope>;

export const ClosureView = z.object({
  cancelled_at: z.string().nullable(),
  decided_at: z.string().nullable(),
  decision_note: z.string().nullable(),
  id: z.string(),
  reason: z.string().nullable(),
  requested_at: z.string(),
  status: z.enum(["requested", "prepared", "closed", "declined", "cancelled"]),
});
export type ClosureView = z.infer<typeof ClosureView>;

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

export const ClientClosureView = z.object({
  request: ClosureView.nullable(),
  standing: StandingView,
});
export type ClientClosureView = z.infer<typeof ClientClosureView>;

export const ClientComplaint = z.object({
  category: z.enum(["payments", "withdrawals", "investments", "identity", "account", "fees", "service", "other"]),
  closed_at: z.string().nullable(),
  details: z.string(),
  due_on: z.string(),
  id: z.string(),
  outcome: z.enum(["upheld", "partly_upheld", "not_upheld"]).nullable(),
  received_at: z.string(),
  reference: z.string(),
  resolution: z.string().nullable(),
  status: z.enum(["new", "investigating", "waiting_for_client", "escalated", "closed"]),
  summary: z.string(),
});
export type ClientComplaint = z.infer<typeof ClientComplaint>;

export const ClientComplaints = z.object({
  complaints: z.array(ClientComplaint),
});
export type ClientComplaints = z.infer<typeof ClientComplaints>;

export const Currency = z.enum(["UGX", "USD"]);
export type Currency = z.infer<typeof Currency>;

export const InvestmentStatus = z.enum(["awaiting_payment", "active", "cancelled", "matured", "withdrawn", "unwound", "transferred"]);
export type InvestmentStatus = z.infer<typeof InvestmentStatus>;

export const ClientInvestmentOut = z.object({
  amount_minor: z.number().int(),
  at_maturity: AtMaturity.nullable(),
  can_change_maturity: z.boolean(),
  can_send: z.boolean().optional(),
  can_withdraw: z.boolean(),
  client_rate_percent: z.string(),
  currency: Currency,
  days_earned: z.number().int(),
  id: z.string(),
  interest_at_maturity_minor: z.number().int().nullable(),
  interest_so_far_minor: z.number().int(),
  interest_today_minor: z.number().int(),
  maturity_date: z.string().nullable(),
  pay_by: z.string(),
  payout_destination_id: z.string().nullable(),
  payout_id: z.string().nullable(),
  product_code: z.string(),
  product_name: z.string(),
  rolled_into_id: z.string().nullable(),
  start_date: z.string().nullable(),
  status: InvestmentStatus,
  term_days: z.number().int().nullable(),
  total_at_maturity_minor: z.number().int().nullable(),
  value_minor: z.number().int(),
  waiting_for_destination: z.boolean(),
  withdrawal_requested_at: z.string().nullable(),
});
export type ClientInvestmentOut = z.infer<typeof ClientInvestmentOut>;

export const ClientLoginStart = z.object({
  email: z.string().nullable().optional(),
  password: z.string(),
  phone: z.string().nullable().optional(),
});
export type ClientLoginStart = z.infer<typeof ClientLoginStart>;

export const DocumentKind = z.enum(["certificate_of_incorporation", "board_resolution", "constitution", "group_resolution"]);
export type DocumentKind = z.infer<typeof DocumentKind>;

export const DocumentOut = z.object({
  byte_size: z.number().int(),
  content_type: z.string(),
  kind: DocumentKind,
  uploaded_at: z.string(),
});
export type DocumentOut = z.infer<typeof DocumentOut>;

export const Problem = z.enum(["signatories_waiting", "too_few_signatories", "approvals_more_than_signatories", "approvals_too_few", "documents_missing", "registration_number_missing"]);
export type Problem = z.infer<typeof Problem>;

export const SignatoryStatus = z.enum(["invited", "joined", "declined", "removed"]);
export type SignatoryStatus = z.infer<typeof SignatoryStatus>;

export const SignatoryOut = z.object({
  account_number: z.string(),
  answered_at: z.string().nullable(),
  client_id: z.string(),
  invited_at: z.string(),
  is_applicant: z.boolean(),
  name: z.string(),
  role: z.string(),
  status: SignatoryStatus,
});
export type SignatoryOut = z.infer<typeof SignatoryOut>;

export const Status = z.enum(["draft", "submitted", "checked", "approved", "rejected"]);
export type Status = z.infer<typeof Status>;

export const ClientOrganisationOut = z.object({
  account_number: z.string().nullable(),
  approvals_needed: z.number().int(),
  created_at: z.string(),
  decided_at: z.string().nullable(),
  documents: z.array(DocumentOut),
  documents_needed: z.array(DocumentKind),
  id: z.string(),
  invited: z.boolean(),
  is_applicant: z.boolean(),
  kind: Kind,
  name: z.string(),
  problems: z.array(Problem),
  registration_number: z.string().nullable(),
  return_note: z.string().nullable(),
  returned_at: z.string().nullable(),
  signatories: z.array(SignatoryOut),
  status: Status,
  submitted_at: z.string().nullable(),
});
export type ClientOrganisationOut = z.infer<typeof ClientOrganisationOut>;

export const ClientOrganisationList = z.object({
  items: z.array(ClientOrganisationOut),
});
export type ClientOrganisationList = z.infer<typeof ClientOrganisationList>;

export const ClientSessionView = z.object({
  client_id: z.string(),
  email: z.string().nullable().optional(),
  email_verified: z.boolean().optional(),
  locked: z.boolean().optional(),
  phone: z.string(),
  pin_set: z.boolean().optional(),
  restricted_until: z.string().nullable(),
});
export type ClientSessionView = z.infer<typeof ClientSessionView>;

export const ClientSignedIn = z.object({
  absolute_expires_at: z.string(),
  idle_expires_at: z.string(),
  session: ClientSessionView,
  token: z.string().nullable(),
});
export type ClientSignedIn = z.infer<typeof ClientSignedIn>;

export const TransferStatus = z.enum(["waiting", "held", "release_proposed", "done", "failed", "declined", "cancelled"]);
export type TransferStatus = z.infer<typeof TransferStatus>;

export const ClientTransferOut = z.object({
  amount_minor: z.number().int(),
  currency: Currency,
  direction: z.string(),
  failure_code: z.string().nullable(),
  finished_at: z.string().nullable(),
  id: z.string(),
  investment_id: z.string().nullable(),
  kept_investment_id: z.string().nullable(),
  message: z.string().nullable(),
  other_name: z.string(),
  requested_at: z.string(),
  status: TransferStatus,
  whole: z.boolean(),
});
export type ClientTransferOut = z.infer<typeof ClientTransferOut>;

export const CodeAnswer = z.object({
  code: z.string(),
});
export type CodeAnswer = z.infer<typeof CodeAnswer>;

export const ConfirmBody = z.object({
  action: z.enum(["withdrawal", "payout_account"]),
  pin: z.string(),
});
export type ConfirmBody = z.infer<typeof ConfirmBody>;

export const ConfirmCode = z.object({
  code: z.string(),
});
export type ConfirmCode = z.infer<typeof ConfirmCode>;

export const DestinationOut = z.object({
  account: z.string(),
  bank_code: z.string().nullable(),
  created_at: z.string(),
  first_paid_at: z.string().nullable(),
  holder_name: z.string(),
  id: z.string(),
  kind: z.enum(["mobile_money", "bank"]),
  name_check: z.enum(["checking", "not_answered", "limit_reached", "waiting_for_id"]).nullable().optional(),
  network: z.enum(["mtn", "airtel"]).nullable(),
  payable_from: z.string().nullable(),
  proof_kind: z.enum(["bank_statement", "bank_letter", "bank_card", "network_registered_name"]).nullable(),
  rejection_reason: z.enum(["name_different", "proof_unreadable", "proof_too_old", "proof_wrong_account", "proof_wrong_document", "suspected_fraud", "network_name_unavailable"]).nullable(),
  status: z.enum(["awaiting_code", "awaiting_proof", "in_review", "approved", "rejected"]),
});
export type DestinationOut = z.infer<typeof DestinationOut>;

export const DetailsBody = z.object({
  date_of_birth: z.string(),
  document_expires_on: z.string(),
  document_number: z.string(),
  given_names: z.string(),
  surname: z.string(),
  tin: z.string().nullable().optional(),
});
export type DetailsBody = z.infer<typeof DetailsBody>;

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

export const DocumentBody = z.object({
  document_country: z.string(),
  document_type: DocumentType,
});
export type DocumentBody = z.infer<typeof DocumentBody>;

export const DocumentItem = z.object({
  byte_size: z.number().int().nullable(),
  date_from: z.string().nullable(),
  date_to: z.string().nullable(),
  file_name: z.string().nullable(),
  finished_at: z.string().nullable(),
  id: z.string(),
  kind: z.enum(["monthly_statement", "requested_statement", "receipt", "tax_certificate", "investment_note"]),
  not_final_from: z.string().nullable(),
  requested_at: z.string(),
  status: z.enum(["pending", "ready", "failed"]),
});
export type DocumentItem = z.infer<typeof DocumentItem>;

export const DocumentList = z.object({
  items: z.array(DocumentItem),
});
export type DocumentList = z.infer<typeof DocumentList>;

export const EmailConfirm = z.object({
  token: z.string(),
});
export type EmailConfirm = z.infer<typeof EmailConfirm>;

export const EmailLinkSent = z.object({
  sent: z.boolean(),
});
export type EmailLinkSent = z.infer<typeof EmailLinkSent>;

export const FreezeBody = z.object({
  reason: z.string().nullable().optional(),
});
export type FreezeBody = z.infer<typeof FreezeBody>;

export const Frequency = z.enum(["weekly", "monthly"]);
export type Frequency = z.infer<typeof Frequency>;

export const GoalCategory = z.enum(["school_fees", "emergency", "land_or_home", "business", "wedding", "retirement", "other"]);
export type GoalCategory = z.infer<typeof GoalCategory>;

export const GoalDetails = z.object({
  category: GoalCategory,
  currency: Currency.optional(),
  name: z.string(),
  target_date: z.string().nullable().optional(),
  target_minor: z.number().int(),
});
export type GoalDetails = z.infer<typeof GoalDetails>;

export const GoalStatus = z.enum(["active", "closed"]);
export type GoalStatus = z.infer<typeof GoalStatus>;

export const Standing = z.enum(["reached", "on_track", "behind", "no_date"]);
export type Standing = z.infer<typeof Standing>;

export const ProgressView = z.object({
  awaiting_payment_minor: z.number().int(),
  interest_minor: z.number().int(),
  percent: z.number().int(),
  standing: Standing,
  value_minor: z.number().int(),
});
export type ProgressView = z.infer<typeof ProgressView>;

export const Network = z.enum(["mtn", "airtel"]);
export type Network = z.infer<typeof Network>;

export const PlanStatus = z.enum(["active", "paused", "stopped"]);
export type PlanStatus = z.infer<typeof PlanStatus>;

export const SavingRuleView = z.object({
  amount_minor: z.number().int(),
  created_at: z.string(),
  currency: Currency,
  day: z.number().int(),
  network: Network,
  next_run_on: z.string().nullable(),
  payer_msisdn: z.string(),
  plan_id: z.string(),
  status: PlanStatus,
});
export type SavingRuleView = z.infer<typeof SavingRuleView>;

export const SavingView = z.object({
  amount_minor: z.number().int(),
  at_maturity: AtMaturity.nullable(),
  interest_minor: z.number().int(),
  investment_id: z.string(),
  maturity_date: z.string().nullable(),
  pay_by: z.string(),
  product_code: z.string(),
  product_name: z.string(),
  rate_percent: z.string(),
  start_date: z.string().nullable(),
  status: InvestmentStatus,
  value_minor: z.number().int(),
});
export type SavingView = z.infer<typeof SavingView>;

export const GoalsApiPlanView = z.object({
  currency: Currency,
  first_on: z.string(),
  interest_minor: z.number().int(),
  last_on: z.string(),
  product_code: z.string(),
  product_name: z.string(),
  put_in_minor: z.number().int(),
  raised_to_minimum: z.boolean(),
  rate_percent: z.string(),
  saving_minor: z.number().int(),
  savings: z.number().int(),
  term_days: z.number().int(),
  without_investing_minor: z.number().int(),
});
export type GoalsApiPlanView = z.infer<typeof GoalsApiPlanView>;

export const GoalView = z.object({
  category: GoalCategory,
  created_at: z.string(),
  currency: Currency,
  goal_id: z.string(),
  name: z.string(),
  payday: z.number().int().nullable(),
  plan: GoalsApiPlanView.nullable(),
  product_code: z.string(),
  progress: ProgressView,
  saving_rule: SavingRuleView.nullable(),
  savings: z.array(SavingView),
  status: GoalStatus,
  target_date: z.string().nullable(),
  target_minor: z.number().int(),
});
export type GoalView = z.infer<typeof GoalView>;

export const GoalList = z.object({
  goals: z.array(GoalView),
});
export type GoalList = z.infer<typeof GoalList>;

export const GroupProgressView = z.object({
  percent: z.number().int(),
  standing: Standing,
  value_minor: z.number().int(),
});
export type GroupProgressView = z.infer<typeof GroupProgressView>;

export const GroupStatus = z.enum(["active", "closed"]);
export type GroupStatus = z.infer<typeof GroupStatus>;

export const MemberView = z.object({
  display_name: z.string(),
  is_owner: z.boolean(),
  is_you: z.boolean(),
  joined_at: z.string(),
  member_id: z.string().nullable(),
});
export type MemberView = z.infer<typeof MemberView>;

export const GroupView = z.object({
  category: GoalCategory,
  created_at: z.string(),
  currency: Currency,
  group_id: z.string(),
  invite_expires_at: z.string().nullable(),
  is_owner: z.boolean(),
  members: z.array(MemberView),
  name: z.string(),
  others_as_of: z.string().nullable(),
  own_goal_id: z.string(),
  progress: GroupProgressView,
  status: GroupStatus,
  target_date: z.string().nullable(),
  target_minor: z.number().int(),
});
export type GroupView = z.infer<typeof GroupView>;

export const GroupList = z.object({
  groups: z.array(GroupView),
});
export type GroupList = z.infer<typeof GroupList>;

export const Health = z.object({
  status: z.literal("ok"),
});
export type Health = z.infer<typeof Health>;

export const MonthView = z.object({
  earned_minor: z.number().int(),
  invested_minor: z.number().int(),
  month: z.string(),
  paid_out_minor: z.number().int(),
});
export type MonthView = z.infer<typeof MonthView>;

export const UpcomingKind = z.enum(["maturity", "autoinvest_run", "goal_saving", "goal_date"]);
export type UpcomingKind = z.infer<typeof UpcomingKind>;

export const UpcomingView = z.object({
  amount_minor: z.number().int(),
  at_maturity: AtMaturity.nullable(),
  goal_id: z.string().nullable(),
  goal_name: z.string().nullable(),
  investment_id: z.string().nullable(),
  kind: UpcomingKind,
  on: z.string(),
  plan_id: z.string().nullable(),
  product_name: z.string().nullable(),
});
export type UpcomingView = z.infer<typeof UpcomingView>;

export const InsightsView = z.object({
  currency: z.string(),
  earned_so_far_minor: z.number().int(),
  invested_now_minor: z.number().int(),
  months: z.array(MonthView),
  upcoming: z.array(UpcomingView),
});
export type InsightsView = z.infer<typeof InsightsView>;

export const InterestPaid = z.enum(["at_maturity", "monthly"]);
export type InterestPaid = z.infer<typeof InterestPaid>;

export const InvestmentRequest = z.object({
  amount_minor: z.number().int(),
  at_maturity: AtMaturity.nullable().optional(),
  currency: Currency,
  product_code: z.string(),
});
export type InvestmentRequest = z.infer<typeof InvestmentRequest>;

export const Invite = z.object({
  code: z.string(),
});
export type Invite = z.infer<typeof Invite>;

export const InviteBody = z.object({
  role: z.string(),
  to: z.string(),
});
export type InviteBody = z.infer<typeof InviteBody>;

export const InvitePreview = z.object({
  category: GoalCategory,
  currency: Currency,
  expires_at: z.string(),
  group_id: z.string(),
  members: z.number().int(),
  name: z.string(),
  owner_name: z.string(),
  target_date: z.string().nullable(),
  target_minor: z.number().int(),
});
export type InvitePreview = z.infer<typeof InvitePreview>;

export const InviteView = z.object({
  code: z.string(),
  expires_at: z.string(),
});
export type InviteView = z.infer<typeof InviteView>;

export const JoinGroup = z.object({
  code: z.string(),
  currency: Currency.optional(),
  display_name: z.string(),
  own_target_minor: z.number().int(),
});
export type JoinGroup = z.infer<typeof JoinGroup>;

export const LimitsOut = z.object({
  bank_payout_day: z.string(),
  bank_per_day: MoneyView.nullable(),
  bank_per_payout: MoneyView.nullable(),
  effective_from: z.string(),
  mobile_money_per_day: MoneyView.nullable(),
  mobile_money_per_payout: MoneyView,
});
export type LimitsOut = z.infer<typeof LimitsOut>;

export const LinkView = z.object({
  id: z.string(),
  kind: z.enum(["deposit", "payout", "investment", "identity_check", "payout_destination", "statement", "security", "conversation", "complaint", "goal", "organisation", "autoinvest_plan"]),
});
export type LinkView = z.infer<typeof LinkView>;

export const LivenessStatus = z.enum(["starting", "open", "passed", "failed", "not_started", "expired"]);
export type LivenessStatus = z.infer<typeof LivenessStatus>;

export const LivenessAttempt = z.object({
  expires_at: z.string(),
  failure_code: z.string().nullable(),
  id: z.string(),
  photo_used: z.boolean(),
  status: LivenessStatus,
  url: z.string().nullable(),
});
export type LivenessAttempt = z.infer<typeof LivenessAttempt>;

export const LivenessState = z.object({
  attempts_left: z.number().int(),
  available: z.boolean(),
  latest: LivenessAttempt.nullable(),
  problem: z.string().nullable(),
});
export type LivenessState = z.infer<typeof LivenessState>;

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

export const MakeComplaint = z.object({
  category: z.enum(["payments", "withdrawals", "investments", "identity", "account", "fees", "service", "other"]),
  client_key: z.string(),
  details: z.string(),
  summary: z.string(),
});
export type MakeComplaint = z.infer<typeof MakeComplaint>;

export const MarkRead = z.object({
  ids: z.array(z.string()).nullable().optional(),
});
export type MarkRead = z.infer<typeof MarkRead>;

export const MaturityChoice = z.object({
  at_maturity: AtMaturity,
  destination_id: z.string().nullable().optional(),
});
export type MaturityChoice = z.infer<typeof MaturityChoice>;

export const MobileMoneyPayment = z.object({
  network: z.enum(["mtn", "airtel"]),
});
export type MobileMoneyPayment = z.infer<typeof MobileMoneyPayment>;

export const NewDestination = z.object({
  account_number: z.string().nullable().optional(),
  bank_code: z.string().nullable().optional(),
  holder_name: z.string(),
  kind: z.enum(["mobile_money", "bank"]),
  phone: z.string().nullable().optional(),
  pin_confirmation: z.string().nullable().optional(),
});
export type NewDestination = z.infer<typeof NewDestination>;

export const NewGroup = z.object({
  category: GoalCategory,
  currency: Currency.optional(),
  display_name: z.string(),
  name: z.string(),
  own_target_minor: z.number().int(),
  target_date: z.string().nullable().optional(),
  target_minor: z.number().int(),
});
export type NewGroup = z.infer<typeof NewGroup>;

export const NewPasskey = z.object({
  credential: z.record(z.string(), z.unknown()),
  name: z.string().optional(),
});
export type NewPasskey = z.infer<typeof NewPasskey>;

export const NewSaving = z.object({
  amount_minor: z.number().int(),
  currency: Currency.optional(),
});
export type NewSaving = z.infer<typeof NewSaving>;

export const NewSavingRule = z.object({
  amount_minor: z.number().int(),
  currency: Currency.optional(),
  day: z.number().int(),
  network: Network,
});
export type NewSavingRule = z.infer<typeof NewSavingRule>;

export const Relationship = z.enum(["spouse", "child", "parent", "sibling", "other_relative", "friend", "other"]);
export type Relationship = z.infer<typeof Relationship>;

export const PersonBody = z.object({
  full_name: z.string(),
  id_number: z.string(),
  phone: z.string(),
  relationship: Relationship,
});
export type PersonBody = z.infer<typeof PersonBody>;

export const NextOfKinBody = z.object({
  people: z.array(PersonBody),
});
export type NextOfKinBody = z.infer<typeof NextOfKinBody>;

export const KycProfileApiPersonView = z.object({
  full_name: z.string(),
  id: z.string(),
  id_number: z.string(),
  phone: z.string(),
  relationship: Relationship,
});
export type KycProfileApiPersonView = z.infer<typeof KycProfileApiPersonView>;

export const NextOfKinView = z.object({
  people: z.array(KycProfileApiPersonView),
  updated_at: z.string().nullable(),
});
export type NextOfKinView = z.infer<typeof NextOfKinView>;

export const NotificationItem = z.object({
  body: z.string(),
  category: z.enum(["money", "investments", "earnings", "account", "security", "support", "news"]),
  created_at: z.string(),
  event: z.string(),
  id: z.string(),
  link: LinkView.nullable(),
  read: z.boolean(),
  title: z.string(),
});
export type NotificationItem = z.infer<typeof NotificationItem>;

export const NotificationPage = z.object({
  items: z.array(NotificationItem),
  next_before: z.string().nullable(),
  unread: z.number().int(),
});
export type NotificationPage = z.infer<typeof NotificationPage>;

export const OptionView = z.object({
  code: z.string(),
});
export type OptionView = z.infer<typeof OptionView>;

export const PasskeyItem = z.object({
  created_at: z.string(),
  id: z.string(),
  last_used_at: z.string().nullable(),
  name: z.string(),
});
export type PasskeyItem = z.infer<typeof PasskeyItem>;

export const PasskeyOptions = z.object({
  public_key: z.record(z.string(), z.unknown()),
});
export type PasskeyOptions = z.infer<typeof PasskeyOptions>;

export const PayOutRequest = z.object({
  destination_id: z.string(),
  pin_confirmation: z.string().nullable().optional(),
});
export type PayOutRequest = z.infer<typeof PayOutRequest>;

export const Payday = z.object({
  day: z.number().int().nullable(),
});
export type Payday = z.infer<typeof Payday>;

export const ReceiptView = z.object({
  bank_name: z.string(),
  byte_size: z.number().int(),
  content_type: z.string(),
  sent_on: z.string(),
  transaction_number: z.string(),
});
export type ReceiptView = z.infer<typeof ReceiptView>;

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

export const PaymentPage = z.object({
  items: z.array(PaymentView),
  next_before: z.string().nullable(),
});
export type PaymentPage = z.infer<typeof PaymentPage>;

export const PayoutOut = z.object({
  amount: MoneyView,
  business_date: z.string(),
  created_at: z.string(),
  destination_id: z.string(),
  id: z.string(),
  kind: z.enum(["mobile_money", "bank"]),
  resent_as: z.string().nullable().optional(),
  returned_at: z.string().nullable().optional(),
  status: z.enum(["in_review", "processing", "paid", "failed", "cancelled"]),
});
export type PayoutOut = z.infer<typeof PayoutOut>;

export const PinBody = z.object({
  pin: z.string(),
});
export type PinBody = z.infer<typeof PinBody>;

export const PinChecked = z.object({
  tries_left: z.number().int(),
  unlocked: z.boolean(),
});
export type PinChecked = z.infer<typeof PinChecked>;

export const PinConfirmed = z.object({
  confirmation: z.string().nullable(),
  confirmed: z.boolean(),
  tries_left: z.number().int(),
});
export type PinConfirmed = z.infer<typeof PinConfirmed>;

export const PlanDetails = z.object({
  amount_minor: z.number().int(),
  at_maturity: AtMaturity.nullable().optional(),
  currency: Currency.optional(),
  day: z.number().int(),
  frequency: Frequency,
  network: Network,
  phone: z.string().nullable().optional(),
  product_code: z.string(),
});
export type PlanDetails = z.infer<typeof PlanDetails>;

export const RunStatus = z.enum(["due", "requested", "paid", "failed", "missed"]);
export type RunStatus = z.infer<typeof RunStatus>;

export const RunView = z.object({
  attempt: z.number().int(),
  code: z.string().nullable(),
  due_on: z.string(),
  investment_id: z.string().nullable(),
  retry_on: z.string().nullable(),
  run_id: z.string(),
  status: RunStatus,
});
export type RunView = z.infer<typeof RunView>;

export const AutoinvestApiPlanView = z.object({
  amount_minor: z.number().int(),
  at_maturity: AtMaturity.nullable(),
  created_at: z.string(),
  currency: Currency,
  day: z.number().int(),
  frequency: Frequency,
  network: Network,
  next_run_on: z.string().nullable(),
  paused_at: z.string().nullable(),
  payer_msisdn: z.string(),
  plan_id: z.string(),
  product_code: z.string(),
  product_name: z.string(),
  runs: z.array(RunView),
  status: PlanStatus,
  stopped_at: z.string().nullable(),
});
export type AutoinvestApiPlanView = z.infer<typeof AutoinvestApiPlanView>;

export const PlanList = z.object({
  plans: z.array(AutoinvestApiPlanView),
});
export type PlanList = z.infer<typeof PlanList>;

export const PortfolioMaturityView = z.object({
  at_maturity: AtMaturity.nullable(),
  days_earned: z.number().int(),
  days_left: z.number().int(),
  investment_id: z.string(),
  on: z.string(),
  product_name: z.string(),
  term_days: z.number().int(),
});
export type PortfolioMaturityView = z.infer<typeof PortfolioMaturityView>;

export const PortfolioPartView = z.object({
  count: z.number().int(),
  worth_minor: z.number().int(),
});
export type PortfolioPartView = z.infer<typeof PortfolioPartView>;

export const PortfolioProductView = z.object({
  count: z.number().int(),
  percent: z.number().int(),
  product_code: z.string(),
  product_name: z.string(),
  worth_minor: z.number().int(),
});
export type PortfolioProductView = z.infer<typeof PortfolioProductView>;

export const PortfolioView = z.object({
  active_count: z.number().int(),
  awaiting_payment_count: z.number().int(),
  currency: z.string(),
  earning_per_day_minor: z.number().int(),
  in_goals: PortfolioPartView,
  interest_so_far_minor: z.number().int(),
  interest_today_minor: z.number().int(),
  invested_minor: z.number().int(),
  next_maturity: PortfolioMaturityView.nullable(),
  open_goals: z.number().int(),
  outside_goals: PortfolioPartView,
  products: z.array(PortfolioProductView),
  worth_minor: z.number().int(),
});
export type PortfolioView = z.infer<typeof PortfolioView>;

export const PreferenceChange = z.object({
  category: z.enum(["money", "investments", "earnings", "account", "security", "support", "news"]),
  channels: z.array(z.enum(["push", "sms", "email"])),
});
export type PreferenceChange = z.infer<typeof PreferenceChange>;

export const Preferences = z.object({
  categories: z.array(CategoryChannels),
});
export type Preferences = z.infer<typeof Preferences>;

export const ProblemReport = z.object({
  description: z.string(),
});
export type ProblemReport = z.infer<typeof ProblemReport>;

export const ProductKind = z.enum(["treasury_bill", "fixed_income"]);
export type ProductKind = z.infer<typeof ProductKind>;

export const ProductStatus = z.enum(["draft", "live", "paused"]);
export type ProductStatus = z.infer<typeof ProductStatus>;

export const ProductOut = z.object({
  client_rate_percent: z.string(),
  code: z.string(),
  currency: Currency,
  interest_paid: InterestPaid,
  kind: ProductKind,
  minimum_minor: z.number().int(),
  name: z.string(),
  status: ProductStatus,
  term_days: z.number().int().nullable(),
});
export type ProductOut = z.infer<typeof ProductOut>;

export const PushDevice = z.object({
  platform: z.enum(["ios", "android"]),
  token: z.string(),
});
export type PushDevice = z.infer<typeof PushDevice>;

export const PushDeviceToken = z.object({
  token: z.string(),
});
export type PushDeviceToken = z.infer<typeof PushDeviceToken>;

export const QuestionView = z.object({
  code: z.string(),
  options: z.array(OptionView),
});
export type QuestionView = z.infer<typeof QuestionView>;

export const QuoteOut = z.object({
  amount_minor: z.number().int(),
  currency: Currency,
  interest_minor: z.number().int().nullable(),
  matures_on: z.string().nullable(),
  product: z.string(),
  starts_on: z.string(),
  total_minor: z.number().int().nullable(),
});
export type QuoteOut = z.infer<typeof QuoteOut>;

export const RecipientOut = z.object({
  account_number: z.string(),
  name: z.string(),
});
export type RecipientOut = z.infer<typeof RecipientOut>;

export const RecoveryAsk = z.object({
  contact_phone: z.string(),
  phone: z.string(),
  reason: z.enum(["lost_phone", "new_number", "no_codes", "other"]),
});
export type RecoveryAsk = z.infer<typeof RecoveryAsk>;

export const Rename = z.object({
  display_name: z.string(),
});
export type Rename = z.infer<typeof Rename>;

export const ResendIn = z.object({
  destination_id: z.string(),
});
export type ResendIn = z.infer<typeof ResendIn>;

export const ResetComplete = z.object({
  challenge_id: z.string(),
  code: z.string(),
  password: z.string(),
});
export type ResetComplete = z.infer<typeof ResetComplete>;

export const ResetStart = z.object({
  phone: z.string(),
});
export type ResetStart = z.infer<typeof ResetStart>;

export const ResetStarted = z.object({
  challenge_id: z.string(),
});
export type ResetStarted = z.infer<typeof ResetStarted>;

export const RiskProfileView = z.object({
  due: z.boolean(),
  latest: AnswersView.nullable(),
  questions: z.array(QuestionView),
});
export type RiskProfileView = z.infer<typeof RiskProfileView>;

export const SavePreferences = z.object({
  categories: z.array(PreferenceChange),
});
export type SavePreferences = z.infer<typeof SavePreferences>;

export const SecondSteps = z.object({
  methods: z.array(z.enum(["passkey", "authenticator", "code", "signup"])),
});
export type SecondSteps = z.infer<typeof SecondSteps>;

export const SendLoginCode = z.object({
  channel: z.enum(["sms", "email"]).optional(),
  login_id: z.string(),
});
export type SendLoginCode = z.infer<typeof SendLoginCode>;

export const SendMessage = z.object({
  body: z.string(),
  key: z.string(),
  topic: z.enum(["deposit", "withdrawal", "investment", "identity", "account", "other"]).optional(),
});
export type SendMessage = z.infer<typeof SendMessage>;

export const TransferPurpose = z.enum(["family_support", "gift", "loan_repayment", "business", "other"]);
export type TransferPurpose = z.infer<typeof TransferPurpose>;

export const SendRequest = z.object({
  amount_minor: z.number().int(),
  currency: Currency,
  message: z.string().nullable().optional(),
  purpose: TransferPurpose,
  purpose_note: z.string().nullable().optional(),
  to_account_number: z.string(),
  whole: z.boolean().optional(),
});
export type SendRequest = z.infer<typeof SendRequest>;

export const SignedOut = z.object({
  ended: z.number().int(),
});
export type SignedOut = z.infer<typeof SignedOut>;

export const SignupComplete = z.object({
  password: z.string(),
  ticket: z.string(),
});
export type SignupComplete = z.infer<typeof SignupComplete>;

export const SignupConfirm = z.object({
  challenge_id: z.string(),
  code: z.string(),
});
export type SignupConfirm = z.infer<typeof SignupConfirm>;

export const SignupStart = z.object({
  email: z.string().nullable().optional(),
  phone: z.string(),
});
export type SignupStart = z.infer<typeof SignupStart>;

export const SignupStarted = z.object({
  challenge_id: z.string(),
  phone: z.string(),
});
export type SignupStarted = z.infer<typeof SignupStarted>;

export const SignupTicketView = z.object({
  phone: z.string(),
  ticket: z.string(),
});
export type SignupTicketView = z.infer<typeof SignupTicketView>;

export const StatementRequest = z.object({
  date_from: z.string(),
  date_to: z.string(),
});
export type StatementRequest = z.infer<typeof StatementRequest>;

export const Unread = z.object({
  unread: z.number().int(),
});
export type Unread = z.infer<typeof Unread>;

export const WithdrawalRequest = z.object({
  destination_id: z.string(),
  pin_confirmation: z.string().nullable().optional(),
});
export type WithdrawalRequest = z.infer<typeof WithdrawalRequest>;

export const IdentityFreezeApiFreezeView = z.object({
  frozen_at: z.string(),
});
export type IdentityFreezeApiFreezeView = z.infer<typeof IdentityFreezeApiFreezeView>;

/** Every route, by method and path, with what it takes and what it answers. */
export const operations = {
  "GET /client/account-closure": {
    method: "GET",
    path: "/client/account-closure",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientClosureView,
  },
  "POST /client/account-closure": {
    method: "POST",
    path: "/client/account-closure",
    params: z.object({}),
    query: z.object({}),
    body: AskBody,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: ClientClosureView,
  },
  "POST /client/account-closure/{request_id}/cancellation": {
    method: "POST",
    path: "/client/account-closure/{request_id}/cancellation",
    params: z.object({
    request_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientClosureView,
  },
  "POST /client/account-freeze": {
    method: "POST",
    path: "/client/account-freeze",
    params: z.object({}),
    query: z.object({}),
    body: FreezeBody,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: IdentityFreezeApiFreezeView,
  },
  "GET /client/authenticator": {
    method: "GET",
    path: "/client/authenticator",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: AuthenticatorState,
  },
  "POST /client/authenticator": {
    method: "POST",
    path: "/client/authenticator",
    params: z.object({}),
    query: z.object({}),
    body: ConfirmCode,
    bodyKind: "json",
    idempotent: false,
    status: 204,
    response: null,
  },
  "DELETE /client/authenticator": {
    method: "DELETE",
    path: "/client/authenticator",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /client/authenticator/options": {
    method: "POST",
    path: "/client/authenticator/options",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: AuthenticatorSetupView,
  },
  "GET /client/autoinvest/plans": {
    method: "GET",
    path: "/client/autoinvest/plans",
    params: z.object({}),
    query: z.object({
    include_stopped: z.boolean().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PlanList,
  },
  "POST /client/autoinvest/plans": {
    method: "POST",
    path: "/client/autoinvest/plans",
    params: z.object({}),
    query: z.object({}),
    body: PlanDetails,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: AutoinvestApiPlanView,
  },
  "GET /client/autoinvest/plans/{plan_id}": {
    method: "GET",
    path: "/client/autoinvest/plans/{plan_id}",
    params: z.object({
    plan_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: AutoinvestApiPlanView,
  },
  "PUT /client/autoinvest/plans/{plan_id}": {
    method: "PUT",
    path: "/client/autoinvest/plans/{plan_id}",
    params: z.object({
    plan_id: z.string(),
  }),
    query: z.object({}),
    body: PlanDetails,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: AutoinvestApiPlanView,
  },
  "POST /client/autoinvest/plans/{plan_id}/pause": {
    method: "POST",
    path: "/client/autoinvest/plans/{plan_id}/pause",
    params: z.object({
    plan_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: AutoinvestApiPlanView,
  },
  "POST /client/autoinvest/plans/{plan_id}/resume": {
    method: "POST",
    path: "/client/autoinvest/plans/{plan_id}/resume",
    params: z.object({
    plan_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: AutoinvestApiPlanView,
  },
  "POST /client/autoinvest/plans/{plan_id}/stop": {
    method: "POST",
    path: "/client/autoinvest/plans/{plan_id}/stop",
    params: z.object({
    plan_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: AutoinvestApiPlanView,
  },
  "GET /client/banks": {
    method: "GET",
    path: "/client/banks",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(BankOut),
  },
  "GET /client/claim": {
    method: "GET",
    path: "/client/claim",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClaimStateView,
  },
  "GET /client/claim-check": {
    method: "GET",
    path: "/client/claim-check",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientClaimCheckEnvelope,
  },
  "POST /client/claim-check": {
    method: "POST",
    path: "/client/claim-check",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientClaimCheck,
  },
  "PUT /client/claim-check/photos/{kind}": {
    method: "PUT",
    path: "/client/claim-check/photos/{kind}",
    params: z.object({
    kind: PhotoKind,
  }),
    query: z.object({}),
    body: BodyAddPhotoClientClaimCheckPhotosKindPut,
    bodyKind: "multipart",
    idempotent: false,
    status: 200,
    response: ClientClaimCheck,
  },
  "POST /client/claim-check/submit": {
    method: "POST",
    path: "/client/claim-check/submit",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientClaimCheck,
  },
  "POST /client/claim/complete": {
    method: "POST",
    path: "/client/claim/complete",
    params: z.object({}),
    query: z.object({}),
    body: ClaimComplete,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientSignedIn,
  },
  "POST /client/claim/confirm": {
    method: "POST",
    path: "/client/claim/confirm",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClaimStateView,
  },
  "POST /client/claim/identity": {
    method: "POST",
    path: "/client/claim/identity",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClaimStateView,
  },
  "POST /client/claim/problem": {
    method: "POST",
    path: "/client/claim/problem",
    params: z.object({}),
    query: z.object({}),
    body: ProblemReport,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClaimStateView,
  },
  "POST /client/claim/start": {
    method: "POST",
    path: "/client/claim/start",
    params: z.object({}),
    query: z.object({}),
    body: ClaimStart,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClaimStarted,
  },
  "GET /client/complaints": {
    method: "GET",
    path: "/client/complaints",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientComplaints,
  },
  "POST /client/complaints": {
    method: "POST",
    path: "/client/complaints",
    params: z.object({}),
    query: z.object({}),
    body: MakeComplaint,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: ClientComplaint,
  },
  "GET /client/complaints/{complaint_id}": {
    method: "GET",
    path: "/client/complaints/{complaint_id}",
    params: z.object({
    complaint_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientComplaint,
  },
  "GET /client/data-export": {
    method: "GET",
    path: "/client/data-export",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: null,
  },
  "GET /client/documents": {
    method: "GET",
    path: "/client/documents",
    params: z.object({}),
    query: z.object({
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: DocumentList,
  },
  "GET /client/documents/{document_id}": {
    method: "GET",
    path: "/client/documents/{document_id}",
    params: z.object({
    document_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: DocumentItem,
  },
  "GET /client/documents/{document_id}/file": {
    method: "GET",
    path: "/client/documents/{document_id}/file",
    params: z.object({
    document_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: null,
  },
  "POST /client/email/verification": {
    method: "POST",
    path: "/client/email/verification",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: EmailLinkSent,
  },
  "POST /client/email/verification/confirm": {
    method: "POST",
    path: "/client/email/verification/confirm",
    params: z.object({}),
    query: z.object({}),
    body: EmailConfirm,
    bodyKind: "json",
    idempotent: false,
    status: 204,
    response: null,
  },
  "GET /client/goal-groups": {
    method: "GET",
    path: "/client/goal-groups",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: GroupList,
  },
  "POST /client/goal-groups": {
    method: "POST",
    path: "/client/goal-groups",
    params: z.object({}),
    query: z.object({}),
    body: NewGroup,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: GroupView,
  },
  "POST /client/goal-groups/invites/preview": {
    method: "POST",
    path: "/client/goal-groups/invites/preview",
    params: z.object({}),
    query: z.object({}),
    body: Invite,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: InvitePreview,
  },
  "POST /client/goal-groups/join": {
    method: "POST",
    path: "/client/goal-groups/join",
    params: z.object({}),
    query: z.object({}),
    body: JoinGroup,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: GroupView,
  },
  "GET /client/goal-groups/{group_id}": {
    method: "GET",
    path: "/client/goal-groups/{group_id}",
    params: z.object({
    group_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: GroupView,
  },
  "PUT /client/goal-groups/{group_id}": {
    method: "PUT",
    path: "/client/goal-groups/{group_id}",
    params: z.object({
    group_id: z.string(),
  }),
    query: z.object({}),
    body: GoalDetails,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: GroupView,
  },
  "POST /client/goal-groups/{group_id}/close": {
    method: "POST",
    path: "/client/goal-groups/{group_id}/close",
    params: z.object({
    group_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: GroupView,
  },
  "POST /client/goal-groups/{group_id}/invite": {
    method: "POST",
    path: "/client/goal-groups/{group_id}/invite",
    params: z.object({
    group_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 201,
    response: InviteView,
  },
  "DELETE /client/goal-groups/{group_id}/invite": {
    method: "DELETE",
    path: "/client/goal-groups/{group_id}/invite",
    params: z.object({
    group_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: GroupView,
  },
  "POST /client/goal-groups/{group_id}/leave": {
    method: "POST",
    path: "/client/goal-groups/{group_id}/leave",
    params: z.object({
    group_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "PUT /client/goal-groups/{group_id}/me": {
    method: "PUT",
    path: "/client/goal-groups/{group_id}/me",
    params: z.object({
    group_id: z.string(),
  }),
    query: z.object({}),
    body: Rename,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: GroupView,
  },
  "POST /client/goal-groups/{group_id}/members/{member_id}/remove": {
    method: "POST",
    path: "/client/goal-groups/{group_id}/members/{member_id}/remove",
    params: z.object({
    group_id: z.string(),
    member_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: GroupView,
  },
  "GET /client/goals": {
    method: "GET",
    path: "/client/goals",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: GoalList,
  },
  "POST /client/goals": {
    method: "POST",
    path: "/client/goals",
    params: z.object({}),
    query: z.object({}),
    body: GoalDetails,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: GoalView,
  },
  "GET /client/goals/plan": {
    method: "GET",
    path: "/client/goals/plan",
    params: z.object({}),
    query: z.object({
    target_minor: z.number().int(),
    target_date: z.string(),
    currency: Currency.optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: GoalsApiPlanView.nullable(),
  },
  "GET /client/goals/{goal_id}": {
    method: "GET",
    path: "/client/goals/{goal_id}",
    params: z.object({
    goal_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: GoalView,
  },
  "PUT /client/goals/{goal_id}": {
    method: "PUT",
    path: "/client/goals/{goal_id}",
    params: z.object({
    goal_id: z.string(),
  }),
    query: z.object({}),
    body: GoalDetails,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: GoalView,
  },
  "POST /client/goals/{goal_id}/close": {
    method: "POST",
    path: "/client/goals/{goal_id}/close",
    params: z.object({
    goal_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: GoalView,
  },
  "PUT /client/goals/{goal_id}/payday": {
    method: "PUT",
    path: "/client/goals/{goal_id}/payday",
    params: z.object({
    goal_id: z.string(),
  }),
    query: z.object({}),
    body: Payday,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: GoalView,
  },
  "POST /client/goals/{goal_id}/saving-rule": {
    method: "POST",
    path: "/client/goals/{goal_id}/saving-rule",
    params: z.object({
    goal_id: z.string(),
  }),
    query: z.object({}),
    body: NewSavingRule,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: GoalView,
  },
  "POST /client/goals/{goal_id}/saving-rule/stop": {
    method: "POST",
    path: "/client/goals/{goal_id}/saving-rule/stop",
    params: z.object({
    goal_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: GoalView,
  },
  "POST /client/goals/{goal_id}/savings": {
    method: "POST",
    path: "/client/goals/{goal_id}/savings",
    params: z.object({
    goal_id: z.string(),
  }),
    query: z.object({}),
    body: NewSaving,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: SavingView,
  },
  "GET /client/identity-check": {
    method: "GET",
    path: "/client/identity-check",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientCheckEnvelope,
  },
  "PUT /client/identity-check/details": {
    method: "PUT",
    path: "/client/identity-check/details",
    params: z.object({}),
    query: z.object({}),
    body: DetailsBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientCheck,
  },
  "PUT /client/identity-check/document": {
    method: "PUT",
    path: "/client/identity-check/document",
    params: z.object({}),
    query: z.object({}),
    body: DocumentBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientCheck,
  },
  "GET /client/identity-check/liveness": {
    method: "GET",
    path: "/client/identity-check/liveness",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: LivenessState,
  },
  "POST /client/identity-check/liveness": {
    method: "POST",
    path: "/client/identity-check/liveness",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: LivenessAttempt,
  },
  "PUT /client/identity-check/photos/{kind}": {
    method: "PUT",
    path: "/client/identity-check/photos/{kind}",
    params: z.object({
    kind: PhotoKind,
  }),
    query: z.object({}),
    body: BodyAddPhotoClientIdentityCheckPhotosKindPut,
    bodyKind: "multipart",
    idempotent: false,
    status: 200,
    response: ClientCheck,
  },
  "POST /client/identity-check/submit": {
    method: "POST",
    path: "/client/identity-check/submit",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientCheck,
  },
  "GET /client/insights": {
    method: "GET",
    path: "/client/insights",
    params: z.object({}),
    query: z.object({
    months: z.number().int().optional(),
    currency: Currency.optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: InsightsView,
  },
  "GET /client/investments": {
    method: "GET",
    path: "/client/investments",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(ClientInvestmentOut),
  },
  "POST /client/investments": {
    method: "POST",
    path: "/client/investments",
    params: z.object({}),
    query: z.object({}),
    body: InvestmentRequest,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: ClientInvestmentOut,
  },
  "GET /client/investments/{investment_id}": {
    method: "GET",
    path: "/client/investments/{investment_id}",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientInvestmentOut,
  },
  "PUT /client/investments/{investment_id}/at-maturity": {
    method: "PUT",
    path: "/client/investments/{investment_id}/at-maturity",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: MaturityChoice,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientInvestmentOut,
  },
  "GET /client/investments/{investment_id}/bank-transfer-details": {
    method: "GET",
    path: "/client/investments/{investment_id}/bank-transfer-details",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: BankTransferDetails,
  },
  "POST /client/investments/{investment_id}/pay-out": {
    method: "POST",
    path: "/client/investments/{investment_id}/pay-out",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: PayOutRequest,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientInvestmentOut,
  },
  "GET /client/investments/{investment_id}/payment": {
    method: "GET",
    path: "/client/investments/{investment_id}/payment",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PaymentView,
  },
  "POST /client/investments/{investment_id}/payments/bank-transfer": {
    method: "POST",
    path: "/client/investments/{investment_id}/payments/bank-transfer",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: BodyPayByBankTransferClientInvestmentsInvestmentIdPaymentsBankTransferPost,
    bodyKind: "multipart",
    idempotent: true,
    status: 201,
    response: PaymentView,
  },
  "POST /client/investments/{investment_id}/payments/mobile-money": {
    method: "POST",
    path: "/client/investments/{investment_id}/payments/mobile-money",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: MobileMoneyPayment,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: PaymentView,
  },
  "POST /client/investments/{investment_id}/send": {
    method: "POST",
    path: "/client/investments/{investment_id}/send",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: SendRequest,
    bodyKind: "json",
    idempotent: true,
    status: 201,
    response: ClientTransferOut,
  },
  "POST /client/investments/{investment_id}/withdraw": {
    method: "POST",
    path: "/client/investments/{investment_id}/withdraw",
    params: z.object({
    investment_id: z.string(),
  }),
    query: z.object({}),
    body: WithdrawalRequest,
    bodyKind: "json",
    idempotent: true,
    status: 200,
    response: ClientInvestmentOut,
  },
  "POST /client/login/authenticator/verify": {
    method: "POST",
    path: "/client/login/authenticator/verify",
    params: z.object({}),
    query: z.object({}),
    body: LoginCode,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientSignedIn,
  },
  "POST /client/login/code/send": {
    method: "POST",
    path: "/client/login/code/send",
    params: z.object({}),
    query: z.object({}),
    body: SendLoginCode,
    bodyKind: "json",
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /client/login/code/verify": {
    method: "POST",
    path: "/client/login/code/verify",
    params: z.object({}),
    query: z.object({}),
    body: LoginCode,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientSignedIn,
  },
  "POST /client/login/passkey/options": {
    method: "POST",
    path: "/client/login/passkey/options",
    params: z.object({}),
    query: z.object({}),
    body: LoginPasskeyOptions,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: PasskeyOptions,
  },
  "POST /client/login/passkey/verify": {
    method: "POST",
    path: "/client/login/passkey/verify",
    params: z.object({}),
    query: z.object({}),
    body: LoginPasskey,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientSignedIn,
  },
  "POST /client/login/start": {
    method: "POST",
    path: "/client/login/start",
    params: z.object({}),
    query: z.object({}),
    body: ClientLoginStart,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: LoginStarted,
  },
  "GET /client/next-of-kin": {
    method: "GET",
    path: "/client/next-of-kin",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: NextOfKinView,
  },
  "PUT /client/next-of-kin": {
    method: "PUT",
    path: "/client/next-of-kin",
    params: z.object({}),
    query: z.object({}),
    body: NextOfKinBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: NextOfKinView,
  },
  "GET /client/notification-preferences": {
    method: "GET",
    path: "/client/notification-preferences",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: Preferences,
  },
  "PUT /client/notification-preferences": {
    method: "PUT",
    path: "/client/notification-preferences",
    params: z.object({}),
    query: z.object({}),
    body: SavePreferences,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: Preferences,
  },
  "GET /client/notifications": {
    method: "GET",
    path: "/client/notifications",
    params: z.object({}),
    query: z.object({
    before: z.string().nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: NotificationPage,
  },
  "POST /client/notifications/read": {
    method: "POST",
    path: "/client/notifications/read",
    params: z.object({}),
    query: z.object({}),
    body: MarkRead,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: Unread,
  },
  "GET /client/organisations": {
    method: "GET",
    path: "/client/organisations",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientOrganisationList,
  },
  "POST /client/organisations": {
    method: "POST",
    path: "/client/organisations",
    params: z.object({}),
    query: z.object({}),
    body: ApplyBody,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: ClientOrganisationOut,
  },
  "GET /client/organisations/{organisation_id}": {
    method: "GET",
    path: "/client/organisations/{organisation_id}",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientOrganisationOut,
  },
  "PATCH /client/organisations/{organisation_id}": {
    method: "PATCH",
    path: "/client/organisations/{organisation_id}",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: ChangeBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientOrganisationOut,
  },
  "POST /client/organisations/{organisation_id}/answer": {
    method: "POST",
    path: "/client/organisations/{organisation_id}/answer",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: AnswerBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientOrganisationOut,
  },
  "PUT /client/organisations/{organisation_id}/documents/{kind}": {
    method: "PUT",
    path: "/client/organisations/{organisation_id}/documents/{kind}",
    params: z.object({
    organisation_id: z.string(),
    kind: DocumentKind,
  }),
    query: z.object({}),
    body: BodyAddDocumentClientOrganisationsOrganisationIdDocumentsKindPut,
    bodyKind: "multipart",
    idempotent: false,
    status: 200,
    response: ClientOrganisationOut,
  },
  "POST /client/organisations/{organisation_id}/signatories": {
    method: "POST",
    path: "/client/organisations/{organisation_id}/signatories",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: InviteBody,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: ClientOrganisationOut,
  },
  "DELETE /client/organisations/{organisation_id}/signatories/{client_id}": {
    method: "DELETE",
    path: "/client/organisations/{organisation_id}/signatories/{client_id}",
    params: z.object({
    organisation_id: z.string(),
    client_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientOrganisationOut,
  },
  "POST /client/organisations/{organisation_id}/submission": {
    method: "POST",
    path: "/client/organisations/{organisation_id}/submission",
    params: z.object({
    organisation_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientOrganisationOut,
  },
  "GET /client/passkeys": {
    method: "GET",
    path: "/client/passkeys",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(PasskeyItem),
  },
  "POST /client/passkeys": {
    method: "POST",
    path: "/client/passkeys",
    params: z.object({}),
    query: z.object({}),
    body: NewPasskey,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: PasskeyItem,
  },
  "POST /client/passkeys/options": {
    method: "POST",
    path: "/client/passkeys/options",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PasskeyOptions,
  },
  "DELETE /client/passkeys/{passkey_id}": {
    method: "DELETE",
    path: "/client/passkeys/{passkey_id}",
    params: z.object({
    passkey_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /client/password/reset/complete": {
    method: "POST",
    path: "/client/password/reset/complete",
    params: z.object({}),
    query: z.object({}),
    body: ResetComplete,
    bodyKind: "json",
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /client/password/reset/start": {
    method: "POST",
    path: "/client/password/reset/start",
    params: z.object({}),
    query: z.object({}),
    body: ResetStart,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ResetStarted,
  },
  "GET /client/payments": {
    method: "GET",
    path: "/client/payments",
    params: z.object({}),
    query: z.object({
    before: z.string().nullable().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PaymentPage,
  },
  "GET /client/payments/{payment_id}": {
    method: "GET",
    path: "/client/payments/{payment_id}",
    params: z.object({
    payment_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PaymentView,
  },
  "GET /client/payout-destinations": {
    method: "GET",
    path: "/client/payout-destinations",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(DestinationOut),
  },
  "POST /client/payout-destinations": {
    method: "POST",
    path: "/client/payout-destinations",
    params: z.object({}),
    query: z.object({}),
    body: NewDestination,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: DestinationOut,
  },
  "GET /client/payout-destinations/{destination_id}": {
    method: "GET",
    path: "/client/payout-destinations/{destination_id}",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: DestinationOut,
  },
  "DELETE /client/payout-destinations/{destination_id}": {
    method: "DELETE",
    path: "/client/payout-destinations/{destination_id}",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /client/payout-destinations/{destination_id}/code": {
    method: "POST",
    path: "/client/payout-destinations/{destination_id}/code",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /client/payout-destinations/{destination_id}/code/verify": {
    method: "POST",
    path: "/client/payout-destinations/{destination_id}/code/verify",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: CodeAnswer,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: DestinationOut,
  },
  "POST /client/payout-destinations/{destination_id}/name-check": {
    method: "POST",
    path: "/client/payout-destinations/{destination_id}/name-check",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: DestinationOut,
  },
  "POST /client/payout-destinations/{destination_id}/proof": {
    method: "POST",
    path: "/client/payout-destinations/{destination_id}/proof",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: BodyUploadProofClientPayoutDestinationsDestinationIdProofPost,
    bodyKind: "multipart",
    idempotent: false,
    status: 200,
    response: DestinationOut,
  },
  "GET /client/payouts": {
    method: "GET",
    path: "/client/payouts",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(PayoutOut),
  },
  "GET /client/payouts/{payout_id}": {
    method: "GET",
    path: "/client/payouts/{payout_id}",
    params: z.object({
    payout_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PayoutOut,
  },
  "POST /client/payouts/{payout_id}/resend": {
    method: "POST",
    path: "/client/payouts/{payout_id}/resend",
    params: z.object({
    payout_id: z.string(),
  }),
    query: z.object({}),
    body: ResendIn,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: PayoutOut,
  },
  "PUT /client/pin": {
    method: "PUT",
    path: "/client/pin",
    params: z.object({}),
    query: z.object({}),
    body: PinBody,
    bodyKind: "json",
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /client/pin/confirm": {
    method: "POST",
    path: "/client/pin/confirm",
    params: z.object({}),
    query: z.object({}),
    body: ConfirmBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: PinConfirmed,
  },
  "POST /client/pin/unlock": {
    method: "POST",
    path: "/client/pin/unlock",
    params: z.object({}),
    query: z.object({}),
    body: PinBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: PinChecked,
  },
  "GET /client/portfolio": {
    method: "GET",
    path: "/client/portfolio",
    params: z.object({}),
    query: z.object({
    currency: Currency.optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: PortfolioView,
  },
  "GET /client/products": {
    method: "GET",
    path: "/client/products",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(ProductOut),
  },
  "GET /client/products/{code}/quote": {
    method: "GET",
    path: "/client/products/{code}/quote",
    params: z.object({
    code: z.string(),
  }),
    query: z.object({
    amount_minor: z.number().int(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: QuoteOut,
  },
  "PUT /client/push-devices": {
    method: "PUT",
    path: "/client/push-devices",
    params: z.object({}),
    query: z.object({}),
    body: PushDevice,
    bodyKind: "json",
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /client/push-devices/remove": {
    method: "POST",
    path: "/client/push-devices/remove",
    params: z.object({}),
    query: z.object({}),
    body: PushDeviceToken,
    bodyKind: "json",
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /client/recovery-requests": {
    method: "POST",
    path: "/client/recovery-requests",
    params: z.object({}),
    query: z.object({}),
    body: RecoveryAsk,
    bodyKind: "json",
    idempotent: false,
    status: 202,
    response: z.record(z.string(), z.unknown()),
  },
  "GET /client/risk-profile": {
    method: "GET",
    path: "/client/risk-profile",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: RiskProfileView,
  },
  "PUT /client/risk-profile": {
    method: "PUT",
    path: "/client/risk-profile",
    params: z.object({}),
    query: z.object({}),
    body: AnswersBody,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: RiskProfileView,
  },
  "GET /client/second-steps": {
    method: "GET",
    path: "/client/second-steps",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: SecondSteps,
  },
  "GET /client/session": {
    method: "GET",
    path: "/client/session",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientSessionView,
  },
  "GET /client/sessions": {
    method: "GET",
    path: "/client/sessions",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: DeviceSessionList,
  },
  "POST /client/sessions/sign-out-others": {
    method: "POST",
    path: "/client/sessions/sign-out-others",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: SignedOut,
  },
  "DELETE /client/sessions/{session_id}": {
    method: "DELETE",
    path: "/client/sessions/{session_id}",
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
  "POST /client/sign-out": {
    method: "POST",
    path: "/client/sign-out",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "POST /client/signup/complete": {
    method: "POST",
    path: "/client/signup/complete",
    params: z.object({}),
    query: z.object({}),
    body: SignupComplete,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: ClientSignedIn,
  },
  "POST /client/signup/confirm": {
    method: "POST",
    path: "/client/signup/confirm",
    params: z.object({}),
    query: z.object({}),
    body: SignupConfirm,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: SignupTicketView,
  },
  "POST /client/signup/start": {
    method: "POST",
    path: "/client/signup/start",
    params: z.object({}),
    query: z.object({}),
    body: SignupStart,
    bodyKind: "json",
    idempotent: false,
    status: 200,
    response: SignupStarted,
  },
  "POST /client/statements": {
    method: "POST",
    path: "/client/statements",
    params: z.object({}),
    query: z.object({}),
    body: StatementRequest,
    bodyKind: "json",
    idempotent: true,
    status: 202,
    response: DocumentItem,
  },
  "GET /client/support/chat": {
    method: "GET",
    path: "/client/support/chat",
    params: z.object({}),
    query: z.object({
    after: z.string().nullable().optional(),
    limit: z.number().int().optional(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientChat,
  },
  "POST /client/support/messages": {
    method: "POST",
    path: "/client/support/messages",
    params: z.object({}),
    query: z.object({}),
    body: SendMessage,
    bodyKind: "json",
    idempotent: false,
    status: 201,
    response: MessageItem,
  },
  "POST /client/support/read": {
    method: "POST",
    path: "/client/support/read",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 204,
    response: null,
  },
  "GET /client/transfer-recipients": {
    method: "GET",
    path: "/client/transfer-recipients",
    params: z.object({}),
    query: z.object({
    to: z.string(),
  }),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: RecipientOut,
  },
  "GET /client/transfers": {
    method: "GET",
    path: "/client/transfers",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: z.array(ClientTransferOut),
  },
  "GET /client/transfers/{transfer_id}": {
    method: "GET",
    path: "/client/transfers/{transfer_id}",
    params: z.object({
    transfer_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientTransferOut,
  },
  "POST /client/transfers/{transfer_id}/cancel": {
    method: "POST",
    path: "/client/transfers/{transfer_id}/cancel",
    params: z.object({
    transfer_id: z.string(),
  }),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: ClientTransferOut,
  },
  "GET /client/withdrawal-limits": {
    method: "GET",
    path: "/client/withdrawal-limits",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: LimitsOut,
  },
  "GET /health/live": {
    method: "GET",
    path: "/health/live",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: Health,
  },
  "GET /health/operations": {
    method: "GET",
    path: "/health/operations",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: Health,
  },
  "GET /health/ready": {
    method: "GET",
    path: "/health/ready",
    params: z.object({}),
    query: z.object({}),
    body: null,
    bodyKind: null,
    idempotent: false,
    status: 200,
    response: Health,
  },
} as const;

export type Operations = typeof operations;
export type OperationName = keyof Operations;
