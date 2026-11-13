import type { IconName } from "../lib/icons.ts";

export interface HelpQuestion {
  question: string;
  answer: string;
  /** Lets another page link straight to this answer. */
  id?: string;
}

export interface HelpTopic {
  id: string;
  icon: IconName;
  title: string;
  summary: string;
  questions: HelpQuestion[];
}

/**
 * The help centre. Answers say only what docs/ and the design canvas establish. Where Finvest
 * has not supplied a fact, the answer keeps a [BRACKETED] placeholder.
 */
export const helpTopics: HelpTopic[] = [
  {
    id: "getting-started",
    icon: "phone",
    title: "Getting started",
    summary: "Sign up, ID check, account number",
    questions: [
      {
        question: "What do I need to open an account?",
        answer:
          "Your National ID, a mobile number and about five minutes. You scan the ID and take a selfie in the app or at app.finvestug.com.",
      },
      {
        question: "How long does the identity check take?",
        answer:
          "A member of staff reviews each check. It usually takes [REVIEW TIME], and the app tells you as soon as it is done.",
      },
      {
        question: "What is my Finvest account number?",
        answer:
          "A 10 digit number you are given when your identity check passes. Quote it when you talk to us.",
      },
      {
        id: "existing-clients",
        question: "I invested with Finvest before the new app. Where is my account?",
        answer:
          "We send you a personal link by SMS and email. Open it, confirm your number, and check that your investments are shown correctly.",
      },
    ],
  },
  {
    id: "paying-in",
    icon: "bolt",
    title: "Paying in",
    summary: "Mobile money, bank transfer, receipts",
    questions: [
      {
        question: "How long does a mobile money payment take?",
        answer: "Usually seconds after you approve the prompt on your phone.",
      },
      {
        question: "How do I pay by bank transfer?",
        answer:
          "Send from any bank using your personal reference and upload the receipt. We match it to the bank statement, and your investment starts when the payment is confirmed.",
      },
      {
        question: "Can I keep money in Finvest without investing it?",
        answer:
          "No. Finvest does not hold a spendable balance for you. Every payment in is for a specific investment or goal.",
      },
    ],
  },
  {
    id: "investments",
    icon: "sun",
    title: "Investments and interest",
    summary: "Rates, tax, maturity, rolling over",
    questions: [
      {
        question: "Why is the rate shown after tax?",
        answer:
          "Tax on interest is taken off before it reaches you, so we show the return you actually keep.",
      },
      {
        question: "Does my rate change after I invest?",
        answer:
          "No. The rate is fixed on the day your investment starts. A new rate applies only to investments that start after it.",
      },
      {
        question: "When do I start earning interest?",
        answer:
          "Interest is earned every day, from the day after your investment starts up to and including the maturity date.",
      },
      {
        question: "What happens when my investment matures?",
        answer:
          "You choose in advance: roll it over into a new bill, or be paid to your mobile money number or bank account.",
      },
    ],
  },
  {
    id: "withdrawals",
    icon: "bank",
    title: "Withdrawals and payouts",
    summary: "Early withdrawal, payout accounts",
    questions: [
      {
        question: "Can I take my money out before maturity?",
        answer:
          "[CONFIRM EARLY WITHDRAWAL TERMS]. You see the exact amount before you confirm, and it can be less than holding to maturity.",
      },
      {
        question: "Where can I be paid?",
        answer:
          "Only to a mobile money number or bank account in your own name. A new bank account needs proof of ownership and a 24 hour wait before first use.",
      },
    ],
  },
  {
    id: "goals",
    icon: "target",
    title: "Goals and groups",
    summary: "Targets, group approvals, companies",
    questions: [
      {
        question: "How does a goal work?",
        answer:
          "Set a target and a date. Finvest works out the monthly amount, reminds you, and puts every payment into treasury bills. Each payment must meet the smallest amount for the product.",
      },
      {
        question: "Who approves a withdrawal from a group account?",
        answer:
          "The group's signatories approve each withdrawal together. One person alone cannot take money out.",
      },
    ],
  },
  {
    id: "security",
    icon: "key",
    title: "Security and sign in",
    summary: "Passwords, passkeys, PIN, devices",
    questions: [
      {
        question: "I forgot my password. What do I do?",
        answer:
          'Choose "Forgot password" on the log in screen. You confirm it is you with your passkey, or with a code and a selfie.',
      },
      {
        question: "Why do I need a second step to sign in?",
        answer:
          "So that a stolen password or SIM card is not enough. A new device needs your password and then a passkey, an authenticator app or a code by SMS. After signing in with an SMS code, withdrawals are limited for 24 hours.",
      },
      {
        question: "Will Finvest ever ask for my password or PIN?",
        answer:
          "Never, by phone, SMS, WhatsApp or email. If someone asks, it is not us. Tell us straight away.",
      },
    ],
  },
];
