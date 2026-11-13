import { Topic } from "./types";

export type Answer = {
  slug: string;
  /** The short label on the chip above the chat. */
  chip: string;
  title: string;
  /** Short paragraphs. They say only what docs/ and the design canvas establish. */
  paragraphs: string[];
  /** The topic a chat started from this answer is filed under. */
  topic: Topic;
};

/**
 * Quick answers shown above the help chat. No answer shows a tax rate or a
 * figure before tax, and none promises a time Finvest has not committed to.
 */
export const answers: Answer[] = [
  {
    slug: "payment-not-showing",
    chip: "Payment not showing",
    title: "My payment is not showing",
    topic: "deposit",
    paragraphs: [
      "A mobile money payment usually shows seconds after you approve the prompt on your phone.",
      "If you approved it and it still says we are checking, do not pay again. We are asking the network what happened, and the app updates as soon as it answers.",
      "If no prompt appeared, dial *165# on MTN or *185# on Airtel and look under pending approvals.",
      "A bank transfer is matched to our bank statement by a member of staff, so it takes longer. Your investment starts when the payment is confirmed.",
    ],
  },
  {
    slug: "withdrawal-times",
    chip: "Withdrawal times",
    title: "How long a withdrawal takes",
    topic: "withdrawal",
    paragraphs: [
      "You can only be paid to a mobile money number or bank account in your own name.",
      "A new payout account is checked by a member of staff first. After it is approved there is a 24 hour wait before its first payout. This protects you if someone else gets into your account.",
      "If you signed in with an SMS code, for 24 hours withdrawals only go to accounts you have used before.",
      "Once a withdrawal is sent we tell you here and by the alerts you chose. If the network returns an error we send it again or return the money to your investment. It is never lost.",
    ],
  },
  {
    slug: "how-interest-works",
    chip: "How interest works",
    title: "How interest works",
    topic: "investment",
    paragraphs: [
      "Interest is earned every day, from the day after your investment starts up to and including the maturity date.",
      "The rate is fixed on the day your investment starts. A new rate applies only to investments that start after it.",
      "Every figure you see is what you keep. Tax on interest is taken off before it reaches you.",
      "When an investment matures you are paid out or it rolls over into a new one, whichever you chose in advance.",
    ],
  },
];

export function answerFor(slug: string): Answer | null {
  return answers.find((answer) => answer.slug === slug) ?? null;
}
