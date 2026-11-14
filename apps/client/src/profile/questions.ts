/** The words for the investing questions. The codes and points live in the backend. */

import { Profile } from "./types";

export type QuestionCopy = {
  title: string;
  options: Record<string, { label: string; detail: string }>;
};

export const QUESTION_COPY: Record<string, QuestionCopy> = {
  purpose: {
    title: "What are you investing for?",
    options: {
      emergency_fund: { label: "Money for emergencies", detail: "I want it safe and close by" },
      planned_purchase: { label: "Something I am planning", detail: "School fees, land, a wedding" },
      grow_savings: { label: "To grow my savings", detail: "No fixed plan yet" },
      retirement_or_children: { label: "Retirement or my children", detail: "Years from now" },
    },
  },
  need_back: {
    title: "When might you need this money back?",
    options: {
      within_3_months: { label: "Within 3 months", detail: "I may need it soon" },
      "3_to_12_months": { label: "In 3 to 12 months", detail: "For something planned this year" },
      "1_to_3_years": { label: "In 1 to 3 years", detail: "I can leave it to grow" },
      over_3_years: { label: "More than 3 years", detail: "This is long term money" },
    },
  },
  early_need: {
    title: "If you needed it before the end of the term, what would you do?",
    options: {
      would_withdraw_early: { label: "Withdraw early", detail: "Even if I earn less" },
      would_use_other_savings: { label: "Use other savings", detail: "I have money elsewhere" },
      would_wait: { label: "Wait for the end", detail: "I would manage without it" },
    },
  },
  experience: {
    title: "Have you invested before?",
    options: {
      none: { label: "Not yet", detail: "This is my first time" },
      savings_or_sacco: { label: "A savings account or SACCO", detail: "Money that earns interest" },
      bills_or_bonds: { label: "Treasury bills or bonds", detail: "Here or with a bank" },
      shares_or_unit_trusts: { label: "Shares or unit trusts", detail: "On the stock exchange or a fund" },
    },
  },
};

export const PROFILE_COPY: Record<Profile, { name: string; about: string }> = {
  careful: {
    name: "Careful",
    about: "You may need this money soon, so shorter Treasury bills of up to 182 days suit you best.",
  },
  balanced: {
    name: "Balanced",
    about: "Some of this money can wait, so Treasury bills of up to a year suit you.",
  },
  long_term: {
    name: "Long term",
    about: "This money can wait, so bills and bonds of any term can suit you.",
  },
};
