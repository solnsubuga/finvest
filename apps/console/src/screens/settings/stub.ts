import { WordingsError, type Category, type Proposal, type Wording, type WordingText, type WordingsApi } from "./api.ts";
import { checkWording, fillSample } from "./rules.ts";

/**
 * The messages for `pnpm dev` and previews, with the words Finvest ships with
 * (backend `domain/wording.py`). Not connected to Finvest. Changes stay in
 * this browser tab. One change is waiting, proposed by someone else, so the
 * second-person step can be tried.
 */

interface Shipped {
  event: string;
  category: Category;
  variables: string[];
  wording: WordingText;
}

const SHIPPED: readonly Shipped[] = [
  { event: "payment_confirmed", category: "money", variables: ["amount", "method"], wording: { title: "Payment received", body: "We received {amount} from {method}.", sms: "Finvest: we received {amount} from {method}." } },
  { event: "payment_failed", category: "money", variables: ["amount", "method"], wording: { title: "Payment did not go through", body: "Your payment of {amount} from {method} did not go through. No money was taken. You can try again in the app.", sms: "Finvest: your payment of {amount} from {method} did not go through. No money was taken." } },
  { event: "payout_processing", category: "money", variables: ["amount", "destination"], wording: { title: "Withdrawal is being processed", body: "{amount} to {destination}. We will confirm when it is sent.", sms: "Finvest: your withdrawal of {amount} to {destination} is being processed." } },
  { event: "payout_sent", category: "money", variables: ["amount", "destination"], wording: { title: "Withdrawal sent", body: "{amount} has been sent to {destination}.", sms: "Finvest: {amount} has been sent to {destination}." } },
  { event: "payout_failed", category: "money", variables: ["amount", "destination"], wording: { title: "Withdrawal could not be sent", body: "We could not send {amount} to {destination}. Your money is safe and our team is on it. You do not need to do anything.", sms: "Finvest: we could not send {amount} to {destination}. Your money is safe and our team is on it." } },
  { event: "investment_started", category: "investments", variables: ["amount", "product"], wording: { title: "Investment started", body: "{amount} is now in a {product}.", sms: "Finvest: {amount} is now invested in a {product}." } },
  { event: "investment_maturing", category: "investments", variables: ["product", "date"], wording: { title: "Your investment matures soon", body: "Your {product} matures on {date}. Choose to reinvest or cash out in the app.", sms: "Finvest: your {product} matures on {date}. Choose to reinvest or cash out in the app." } },
  { event: "investment_matured", category: "investments", variables: ["amount", "product"], wording: { title: "Investment matured", body: "Your {product} has matured at {amount}.", sms: "Finvest: your {product} has matured at {amount}." } },
  { event: "investment_withdrawal_requested", category: "investments", variables: ["amount", "product"], wording: { title: "Early withdrawal requested", body: "We will pay you {amount} from your {product}. We will tell you when it is sent.", sms: "Finvest: we will pay you {amount} from your {product}. We will tell you when it is sent." } },
  { event: "investment_withdrawal_paid", category: "investments", variables: ["amount", "product"], wording: { title: "Early withdrawal sent", body: "{amount} from your {product} has been sent to you.", sms: "Finvest: {amount} from your {product} has been sent to you." } },
  { event: "interest_earned", category: "earnings", variables: ["amount", "count"], wording: { title: "You earned {amount} today", body: "Interest across {count} investments, after tax.", sms: "Finvest: you earned {amount} today across {count} investments, after tax." } },
  { event: "kyc_approved", category: "account", variables: [], wording: { title: "Identity check approved", body: "Your account is fully open. You can now invest.", sms: "Finvest: your identity check is approved. You can now invest." } },
  { event: "kyc_retake_requested", category: "account", variables: [], wording: { title: "We need a photo again", body: "One of your identity photos was not clear enough. Open the app to take it again.", sms: "Finvest: we need one of your identity photos again. Open the app to take it." } },
  { event: "kyc_rejected", category: "account", variables: [], wording: { title: "Identity check not approved", body: "We could not approve your identity check. Open Help in the app and we will explain what happens next.", sms: "Finvest: we could not approve your identity check. Open Help in the app for what happens next." } },
  { event: "payout_destination_approved", category: "account", variables: ["destination"], wording: { title: "Payout account approved", body: "{destination} is approved. You can withdraw to it after the 24 hour wait.", sms: "Finvest: {destination} is approved for withdrawals after the 24 hour wait." } },
  { event: "payout_destination_refused", category: "account", variables: ["destination"], wording: { title: "Payout account not approved", body: "We could not approve {destination}. A payout account must be in your own name.", sms: "Finvest: we could not approve {destination}. A payout account must be in your own name." } },
  { event: "statement_ready", category: "account", variables: ["period"], wording: { title: "{period} statement is ready", body: "Open it in Documents or have it emailed to you.", sms: "Finvest: your {period} statement is ready in the app." } },
  { event: "new_sign_in", category: "security", variables: ["device"], wording: { title: "New sign-in to your account", body: "Your account was signed in on {device}. If this was not you, change your password now and contact us.", sms: "Finvest: your account was signed in on {device}. If this was not you, change your password now." } },
  { event: "password_changed", category: "security", variables: [], wording: { title: "Your password was changed", body: "If this was not you, contact us straight away. Finvest staff will never ask for your password, PIN or codes.", sms: "Finvest: your password was changed. If this was not you, contact us straight away." } },
  { event: "second_step_changed", category: "security", variables: ["change"], wording: { title: "Sign-in settings changed", body: "{change}. If this was not you, contact us straight away.", sms: "Finvest: {change}. If this was not you, contact us straight away." } },
  { event: "support_reply", category: "support", variables: [], wording: { title: "New message from Finvest", body: "Our team replied to you. Open Help to read it.", sms: "Finvest: our team replied to you. Open Help in the app to read it." } },
  { event: "complaint_received", category: "support", variables: ["reference", "date"], wording: { title: "Complaint {reference} received", body: "We have logged your complaint and will reply by {date}. You can follow it in Help.", sms: "Finvest: we have logged your complaint {reference} and will reply by {date}." } },
  { event: "complaint_closed", category: "support", variables: ["reference"], wording: { title: "Complaint {reference} closed", body: "We have recorded the outcome of your complaint. Open Help to read it and tell us if you do not accept it.", sms: "Finvest: complaint {reference} is closed. Open Help in the app to read the outcome." } },];

const preview = (wording: WordingText): WordingText => ({
  title: fillSample(wording.title),
  body: fillSample(wording.body),
  sms: fillSample(wording.sms),
});

export function createStubWordingsApi(me: { id: string }, now: () => number = Date.now): WordingsApi {
  let counter = 0;
  const rows: Wording[] = SHIPPED.map((shipped) => ({
    ...shipped,
    wording: { ...shipped.wording },
    preview: preview(shipped.wording),
    edited: false,
    changedAt: null,
    proposal: null,
  }));
  const proposers = new Map<string, string>();

  function propose(wording: WordingText, reason: string, by: string, at: string): Proposal {
    const id = `sample-proposal-${++counter}`;
    proposers.set(id, by);
    return { id, wording, preview: preview(wording), reason, proposedBy: by, proposedAt: at, yours: by === me.id };
  }

  const maturing = rows.find((row) => row.event === "investment_maturing") as Wording;
  maturing.proposal = propose(
    {
      title: "Your investment matures on {date}",
      body: "Your {product} matures on {date}. Choose to reinvest or cash out in the app before then.",
      sms: "Finvest: your {product} matures on {date}. Reinvest or cash out in the app.",
    },
    "Clients asked for the date in the title.",
    "sample-staff-daniel",
    "2026-11-12T09:30:00Z",
  );

  const copy = (row: Wording): Wording => ({
    ...row,
    wording: { ...row.wording },
    preview: { ...row.preview },
    proposal: row.proposal ? { ...row.proposal, yours: proposers.get(row.proposal.id) === me.id } : null,
  });

  return {
    list: () => Promise.resolve(rows.map(copy)),
    propose(event, wording, reason) {
      const row = rows.find((candidate) => candidate.event === event);
      if (!row) return Promise.reject(new WordingsError("not_found"));
      if (row.proposal) return Promise.reject(new WordingsError("conflict"));
      const problems = checkWording(wording, row.variables);
      if (problems.length > 0 || reason.trim().length === 0) return Promise.reject(new WordingsError("invalid", problems));
      row.proposal = propose({ ...wording }, reason.trim(), me.id, new Date(now()).toISOString());
      return Promise.resolve(copy(row));
    },
    decide(proposalId, decision, reason) {
      const row = rows.find((candidate) => candidate.proposal?.id === proposalId);
      if (!row?.proposal) return Promise.reject(new WordingsError("not_found"));
      const mine = proposers.get(proposalId) === me.id;
      if (decision === "withdrawn" ? !mine : mine) return Promise.reject(new WordingsError("same_person"));
      if (reason.trim().length === 0) return Promise.reject(new WordingsError("invalid"));
      if (decision === "approved") {
        row.wording = { ...row.proposal.wording };
        row.preview = preview(row.wording);
        row.edited = true;
        row.changedAt = new Date(now()).toISOString();
      }
      row.proposal = null;
      return Promise.resolve(copy(row));
    },
  };
}
