// Generated from openapi.json by scripts/generate.ts. Do not edit.
/* eslint-disable */
import { z } from "zod";

/** A file for a multipart upload: a Blob on the web, a local file description on a phone. */
export type Upload = Blob | { uri: string; name: string; type: string };
export const Upload = z.custom<Upload>((value) => value !== null && typeof value === "object");

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

export const PaymentsBankTransferPost = z.object({
  amount_minor: z.number().int(),
  bank_name: z.string(),
  receipt: Upload,
  sent_on: z.string(),
  transaction_number: z.string(),
});
export type PaymentsBankTransferPost = z.infer<typeof PaymentsBankTransferPost>;

export const ProofPost = z.object({
  file: Upload,
  proof_kind: z.enum(["bank_statement", "bank_letter", "bank_card"]),
});
export type ProofPost = z.infer<typeof ProofPost>;

export const CategoryChannels = z.object({
  always_on: z.boolean(),
  category: z.enum(["money", "investments", "earnings", "account", "security", "support", "news"]),
  channels: z.array(z.enum(["push", "sms", "email"])),
});
export type CategoryChannels = z.infer<typeof CategoryChannels>;

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

export const InvestmentStatus = z.enum(["awaiting_payment", "active", "cancelled", "matured"]);
export type InvestmentStatus = z.infer<typeof InvestmentStatus>;

export const ClientInvestmentOut = z.object({
  amount_minor: z.number().int(),
  at_maturity: AtMaturity.nullable(),
  can_change_maturity: z.boolean(),
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
});
export type ClientInvestmentOut = z.infer<typeof ClientInvestmentOut>;

export const ClientLoginStart = z.object({
  password: z.string(),
  phone: z.string(),
});
export type ClientLoginStart = z.infer<typeof ClientLoginStart>;

export const ClientSessionView = z.object({
  client_id: z.string(),
  phone: z.string(),
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
  network: z.enum(["mtn", "airtel"]).nullable(),
  payable_from: z.string().nullable(),
  proof_kind: z.enum(["bank_statement", "bank_letter", "bank_card", "network_registered_name"]).nullable(),
  rejection_reason: z.enum(["name_different", "proof_unreadable", "proof_too_old", "proof_wrong_account", "proof_wrong_document", "suspected_fraud"]).nullable(),
  status: z.enum(["awaiting_code", "awaiting_proof", "in_review", "approved", "rejected"]),
});
export type DestinationOut = z.infer<typeof DestinationOut>;

export const DocumentItem = z.object({
  byte_size: z.number().int().nullable(),
  date_from: z.string().nullable(),
  date_to: z.string().nullable(),
  file_name: z.string().nullable(),
  finished_at: z.string().nullable(),
  id: z.string(),
  kind: z.enum(["monthly_statement", "requested_statement", "receipt"]),
  not_final_from: z.string().nullable(),
  requested_at: z.string(),
  status: z.enum(["pending", "ready", "failed"]),
});
export type DocumentItem = z.infer<typeof DocumentItem>;

export const DocumentList = z.object({
  items: z.array(DocumentItem),
});
export type DocumentList = z.infer<typeof DocumentList>;

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
  plan: GoalsApiPlanView.nullable(),
  product_code: z.string(),
  progress: ProgressView,
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

export const UpcomingView = z.object({
  amount_minor: z.number().int(),
  at_maturity: AtMaturity.nullable(),
  investment_id: z.string(),
  on: z.string(),
  product_name: z.string(),
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

export const MoneyView = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type MoneyView = z.infer<typeof MoneyView>;

export const LimitsOut = z.object({
  bank_per_day: MoneyView.nullable(),
  bank_per_payout: MoneyView.nullable(),
  effective_from: z.string(),
  mobile_money_per_day: MoneyView.nullable(),
  mobile_money_per_payout: MoneyView,
});
export type LimitsOut = z.infer<typeof LimitsOut>;

export const LinkView = z.object({
  id: z.string(),
  kind: z.enum(["deposit", "payout", "investment", "identity_check", "payout_destination", "statement", "security", "conversation", "complaint"]),
});
export type LinkView = z.infer<typeof LinkView>;

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

export const Network = z.enum(["mtn", "airtel"]);
export type Network = z.infer<typeof Network>;

export const NewDestination = z.object({
  account_number: z.string().nullable().optional(),
  bank_code: z.string().nullable().optional(),
  holder_name: z.string(),
  kind: z.enum(["mobile_money", "bank"]),
  phone: z.string().nullable().optional(),
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
});
export type PayOutRequest = z.infer<typeof PayOutRequest>;

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

export const PayoutOut = z.object({
  amount: MoneyView,
  business_date: z.string(),
  created_at: z.string(),
  destination_id: z.string(),
  id: z.string(),
  kind: z.enum(["mobile_money", "bank"]),
  status: z.enum(["in_review", "processing", "paid", "failed", "cancelled"]),
});
export type PayoutOut = z.infer<typeof PayoutOut>;

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

export const PlanStatus = z.enum(["active", "paused", "stopped"]);
export type PlanStatus = z.infer<typeof PlanStatus>;

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

export const PreferenceChange = z.object({
  category: z.enum(["money", "investments", "earnings", "account", "security", "support", "news"]),
  channels: z.array(z.enum(["push", "sms", "email"])),
});
export type PreferenceChange = z.infer<typeof PreferenceChange>;

export const Preferences = z.object({
  categories: z.array(CategoryChannels),
});
export type Preferences = z.infer<typeof Preferences>;

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

export const Rename = z.object({
  display_name: z.string(),
});
export type Rename = z.infer<typeof Rename>;

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

/** Every route, by method and path, with what it takes and what it answers. */
export const operations = {
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
  "POST /client/goal-groups/{group_id}/members/{goal_id}/remove": {
    method: "POST",
    path: "/client/goal-groups/{group_id}/members/{goal_id}/remove",
    params: z.object({
    group_id: z.string(),
    goal_id: z.string(),
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
  "GET /client/insights": {
    method: "GET",
    path: "/client/insights",
    params: z.object({}),
    query: z.object({
    months: z.number().int().optional(),
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
    body: PaymentsBankTransferPost,
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
  "POST /client/payout-destinations/{destination_id}/proof": {
    method: "POST",
    path: "/client/payout-destinations/{destination_id}/proof",
    params: z.object({
    destination_id: z.string(),
  }),
    query: z.object({}),
    body: ProofPost,
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
