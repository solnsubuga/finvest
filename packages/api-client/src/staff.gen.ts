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

export const AmountOutput = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type AmountOutput = z.infer<typeof AmountOutput>;

export const Currency = z.enum(["UGX", "USD"]);
export type Currency = z.infer<typeof Currency>;

export const AmountOut = z.object({
  currency: Currency,
  minor: z.number().int(),
});
export type AmountOut = z.infer<typeof AmountOut>;

export const AmountView = z.object({
  currency: Currency,
  minor: z.number().int(),
});
export type AmountView = z.infer<typeof AmountView>;

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

export const Approval = z.object({
  reason: z.string(),
});
export type Approval = z.infer<typeof Approval>;

export const ApprovalItemOut = z.object({
  can_decide: z.boolean(),
  id: z.string(),
  kind: z.enum(["wording_change", "ledger_correction", "pot_transfer", "bank_transfer_match", "day_close", "dealing_order", "dealing_result", "rate_change", "migration_totals", "migration_sign_off", "phone_change", "team_change", "client_lock", "suppression_lift", "complaint_outcome", "announcement", "payout_account"]),
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

export const AtMaturity = z.enum(["roll_over", "pay_out"]);
export type AtMaturity = z.infer<typeof AtMaturity>;

export const AuthenticatorSetupView = z.object({
  secret: z.string(),
  uri: z.string(),
});
export type AuthenticatorSetupView = z.infer<typeof AuthenticatorSetupView>;

export const AutomaticCheck = z.enum(["document_genuine", "selfie_match", "screening"]);
export type AutomaticCheck = z.infer<typeof AutomaticCheck>;

export const MoneyView = z.object({
  currency: z.enum(["UGX", "USD"]),
  minor: z.number().int(),
});
export type MoneyView = z.infer<typeof MoneyView>;

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
  items: z.array(PaymentView),
  next_before: z.string().nullable(),
});
export type BankTransferPage = z.infer<typeof BankTransferPage>;

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

export const ClaimProgressView = z.object({
  cannot_be_invited: z.number().int(),
  claimed_and_confirmed: z.number().int(),
  invited: z.number().int(),
  not_yet_claimed: z.number().int(),
  said_something_is_wrong: z.number().int(),
});
export type ClaimProgressView = z.infer<typeof ClaimProgressView>;

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

export const Cover = z.enum(["covered", "bid_needed", "on_order", "surplus"]);
export type Cover = z.infer<typeof Cover>;

export const DecideAnnouncement = z.object({
  decision: z.enum(["approved", "rejected", "withdrawn"]),
  reason: z.string().nullable().optional(),
});
export type DecideAnnouncement = z.infer<typeof DecideAnnouncement>;

export const DecideChange = z.object({
  decision: z.enum(["approved", "rejected", "withdrawn"]),
  reason: z.string(),
});
export type DecideChange = z.infer<typeof DecideChange>;

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

export const PositionOut = z.object({
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
export type PositionOut = z.infer<typeof PositionOut>;

export const WithdrawalsOut = z.object({
  asked: AmountOut,
  clients: z.number().int(),
});
export type WithdrawalsOut = z.infer<typeof WithdrawalsOut>;

export const DeskOut = z.object({
  bills_held: AmountOut,
  buffer: BufferOut,
  early_withdrawals: WithdrawalsOut,
  matches_custodian: z.boolean().nullable(),
  money_to_place: AmountOut,
  orders: z.array(OrderOut),
  positions: z.array(PositionOut),
});
export type DeskOut = z.infer<typeof DeskOut>;

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
  figures: z.array(z.number().int()),
  key: z.string(),
  sign_off: SignOffView.nullable(),
});
export type DifferenceView = z.infer<typeof DifferenceView>;

export const DocumentNumber = z.object({
  document_number: z.string(),
});
export type DocumentNumber = z.infer<typeof DocumentNumber>;

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

export const InvestmentStatus = z.enum(["awaiting_payment", "active", "cancelled", "matured", "withdrawn"]);
export type InvestmentStatus = z.infer<typeof InvestmentStatus>;

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

export const MessageItem = z.object({
  author: z.enum(["client", "staff"]),
  author_name: z.string().nullable(),
  body: z.string(),
  conversation_id: z.string(),
  created_at: z.string(),
  id: z.string(),
});
export type MessageItem = z.infer<typeof MessageItem>;

export const MoveComplaint = z.object({
  note: z.string(),
  status: z.enum(["investigating", "waiting_for_client", "escalated"]),
});
export type MoveComplaint = z.infer<typeof MoveComplaint>;

export const NewCheck = z.object({
  proof_name: z.string(),
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

export const TotalLineView = z.object({
  agrees: z.boolean(),
  currency: Currency.nullable(),
  figure: Figure,
  key: z.string(),
  new: z.number().int(),
  old: z.number().int(),
});
export type TotalLineView = z.infer<typeof TotalLineView>;

export const TotalsView = z.object({
  as_of: z.string(),
  clients: z.number().int(),
  confirmed_by: z.string().nullable(),
  entered_at: z.string(),
  entered_by: z.string(),
  id: z.string(),
  interest_before_tax: z.array(AmountView),
  invested: z.array(AmountView),
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
  allowed: z.array(z.enum(["run_trial", "enter_totals", "sign_off"])),
  blockers: z.array(z.enum(["no_trial", "import_has_errors", "totals_not_entered", "totals_not_confirmed", "difference_not_signed_off"])),
  claims: ClaimProgressView.nullable(),
  differences: z.array(DifferenceView),
  lines: z.array(TotalLineView),
  totals: TotalsView.nullable(),
  trial: TrialView.nullable(),
  trials_run: z.number().int(),
});
export type OverviewView = z.infer<typeof OverviewView>;

export const PasskeyOptions = z.object({
  public_key: z.record(z.string(), z.unknown()),
});
export type PasskeyOptions = z.infer<typeof PasskeyOptions>;

export const PersonView = z.object({
  id: z.string(),
  name: z.string(),
});
export type PersonView = z.infer<typeof PersonView>;

export const PeopleList = z.object({
  people: z.array(PersonView),
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

export const ProposalStatus = z.enum(["proposed", "approved", "sent_back", "withdrawn"]);
export type ProposalStatus = z.infer<typeof ProposalStatus>;

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

export const ProposeChange = z.object({
  email: z.string().nullable().optional(),
  kind: z.enum(["invite", "change_role", "deactivate", "reactivate", "reset_sign_in"]),
  name: z.string().nullable().optional(),
  reason: z.string(),
  role: z.enum(["admin", "operations", "finance", "compliance", "support"]).nullable().optional(),
  staff_id: z.string().nullable().optional(),
});
export type ProposeChange = z.infer<typeof ProposeChange>;

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

export const Removal = z.object({
  reason: z.string(),
});
export type Removal = z.infer<typeof Removal>;

export const ResultIn = z.object({
  fill: FillIn.nullable(),
});
export type ResultIn = z.infer<typeof ResultIn>;

export const RoleView = z.object({
  permissions: z.array(z.enum(["overview.view", "review_queue.view", "identity_checks.view", "identity_checks.review", "maturities.view", "support.view", "support.reply", "complaints.view", "complaints.manage", "complaints.close", "clients.view", "clients.lock", "money_movement.view", "reconciliation.view", "investments.view", "automatic_investing.view", "goals.view", "dealing_desk.view", "dealing_orders.change", "finance.view", "products.view", "rates.view", "rates.change", "compliance.view", "team.view", "team.change", "reports.view", "audit.view", "settings.view", "wordings.change", "email_suppressions.lift", "announcements.send", "migration.view", "bank_transfers.change", "payout_accounts.change", "migration.import", "migration.sign_off", "client_phones.change"])),
  role: z.enum(["admin", "operations", "finance", "compliance", "support"]),
});
export type RoleView = z.infer<typeof RoleView>;

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

export const SetTopic = z.object({
  topic: z.enum(["deposit", "withdrawal", "investment", "identity", "account", "other"]),
});
export type SetTopic = z.infer<typeof SetTopic>;

export const SignOffRequest = z.object({
  key: z.string(),
  reason: z.string(),
});
export type SignOffRequest = z.infer<typeof SignOffRequest>;

export const StaffChat = z.object({
  client_id: z.string(),
  conversation: ConversationItem,
  messages: z.array(MessageItem),
});
export type StaffChat = z.infer<typeof StaffChat>;

export const StaffPhoto = z.object({
  id: z.string(),
  kind: PhotoKind,
});
export type StaffPhoto = z.infer<typeof StaffPhoto>;

export const StaffCheck = z.object({
  account_number: z.string().nullable(),
  allowed: z.array(z.enum(["approve", "reject", "ask_for_photos", "escalate"])),
  automatic_checks: z.array(Outcome),
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
  payable_from: z.string().nullable(),
  proof_kind: z.enum(["bank_statement", "bank_letter", "bank_card", "network_registered_name"]).nullable(),
  proof_link: ProofLinkOut.nullable(),
  rejection_reason: z.enum(["name_different", "proof_unreadable", "proof_too_old", "proof_wrong_account", "proof_wrong_document", "suspected_fraud"]).nullable(),
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
  payable_from: z.string().nullable(),
  proof_kind: z.enum(["bank_statement", "bank_letter", "bank_card", "network_registered_name"]).nullable(),
  rejection_reason: z.enum(["name_different", "proof_unreadable", "proof_too_old", "proof_wrong_account", "proof_wrong_document", "suspected_fraud"]).nullable(),
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

export const StaffLoginStart = z.object({
  email: z.string(),
  password: z.string(),
});
export type StaffLoginStart = z.infer<typeof StaffLoginStart>;

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

export const StaffSendMessage = z.object({
  body: z.string(),
  key: z.string(),
});
export type StaffSendMessage = z.infer<typeof StaffSendMessage>;

export const StaffSessionView = z.object({
  name: z.string(),
  permissions: z.array(z.enum(["overview.view", "review_queue.view", "identity_checks.view", "identity_checks.review", "maturities.view", "support.view", "support.reply", "complaints.view", "complaints.manage", "complaints.close", "clients.view", "clients.lock", "money_movement.view", "reconciliation.view", "investments.view", "automatic_investing.view", "goals.view", "dealing_desk.view", "dealing_orders.change", "finance.view", "products.view", "rates.view", "rates.change", "compliance.view", "team.view", "team.change", "reports.view", "audit.view", "settings.view", "wordings.change", "email_suppressions.lift", "announcements.send", "migration.view", "bank_transfers.change", "payout_accounts.change", "migration.import", "migration.sign_off", "client_phones.change"])),
  role: z.enum(["admin", "operations", "finance", "compliance", "support"]),
  staff_id: z.string(),
});
export type StaffSessionView = z.infer<typeof StaffSessionView>;

export const SuppressionList = z.object({
  can_lift: z.boolean(),
  suppressions: z.array(SuppressionView),
});
export type SuppressionList = z.infer<typeof SuppressionList>;

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
    idempotent: false,
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
