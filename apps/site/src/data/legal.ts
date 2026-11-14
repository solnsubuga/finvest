/**
 * The legal documents: structure only. Each section says what it will cover. The wording must
 * come from Finvest's lawyers before launch, and every page says so.
 */

export interface LegalDocument {
  slug: string;
  title: string;
  sections: { heading: string; covers: string }[];
}

export const legalDocuments: LegalDocument[] = [
  {
    slug: "terms",
    title: "Terms and conditions",
    sections: [
      {
        heading: "Who we are",
        covers:
          "The company behind Finvest, who regulates it, and its licence.",
      },
      {
        heading: "Opening an account",
        covers:
          "Who can open an account, the identity checks we must carry out, and how your account number is issued.",
      },
      {
        heading: "Investing",
        covers:
          "How orders are placed, when an investment starts, how rates are set, and how tax is deducted.",
      },
      {
        heading: "Payments and payouts",
        covers:
          "Accepted payment methods, payout accounts in your own name, limits, and what happens when a payment fails.",
      },
      {
        heading: "Early withdrawal",
        covers:
          "How an investment is ended before maturity, and why the amount can differ from the amount at maturity.",
      },
      { heading: "Fees", covers: "The fees that apply, shown before you confirm any action." },
      {
        heading: "Keeping your account safe",
        covers: "Your responsibilities for your password, passkey, PIN and devices, and ours.",
      },
      {
        heading: "Complaints and disputes",
        covers: "How to complain, our timelines, and where to take it next.",
      },
      {
        heading: "Closing your account",
        covers: "How to close once nothing is left invested or owed to you, and the records we keep afterwards.",
      },
    ],
  },
  {
    slug: "privacy",
    title: "Privacy notice",
    sections: [
      {
        heading: "Who is responsible for your data",
        covers:
          "The company responsible for your data as its controller, its registration under the Data Protection and Privacy Act, 2019, and how to reach its data protection officer.",
      },
      {
        heading: "What we collect",
        covers:
          "Identity documents and selfies, contact details, the next of kin you name, your answers to the investing questions, payout accounts, payments and investments, and device and sign-in records.",
      },
      {
        heading: "Why we use it",
        covers:
          "To check who you are, run your investments, meet our legal duties, and keep your account safe.",
      },
      {
        heading: "Who we share it with",
        covers:
          "Payment providers, the custodian, identity check providers, regulators and tax authorities, and the next of kin you named if we cannot reach you. Nobody else.",
      },
      {
        heading: "Where it is kept and for how long",
        covers: "Where data is stored, how it is protected, and how long each kind is kept, including after you close your account.",
      },
      {
        heading: "Your rights",
        covers: "How to download a copy of your data from Profile, correct it or object to its use, and how to complain.",
      },
    ],
  },
  {
    slug: "risk",
    title: "Risk disclosure",
    sections: [
      {
        heading: "What you are buying",
        covers: "What a treasury bill and a fixed income investment are, and who issues them.",
      },
      {
        heading: "Holding to maturity",
        covers: "What you are paid at maturity and what that depends on.",
      },
      {
        heading: "Withdrawing early",
        covers:
          "Why selling before maturity can return less than you expected, or less than you put in.",
      },
      {
        heading: "Rates change",
        covers:
          "Rates are set by auctions. A new rate applies to new investments, so rolling over can earn less than before.",
      },
      {
        heading: "Dollar investments",
        covers: "How the exchange rate affects the value of a dollar investment in shillings.",
      },
      {
        heading: "What Finvest does not promise",
        covers: "Past rates are not a promise of future rates, and Finvest gives no investment advice.",
      },
    ],
  },
  {
    slug: "fees",
    title: "Fees and charges",
    sections: [
      { heading: "Fees to invest", covers: "The fee, if any, to make an investment." },
      { heading: "Fees to be paid out", covers: "The fee, if any, to be paid at maturity." },
      { heading: "Early withdrawal", covers: "The fee, if any, to withdraw before maturity." },
      {
        heading: "Charges from your mobile money provider or bank",
        covers: "Charges that others make and Finvest does not control.",
      },
      {
        heading: "When fees change",
        covers: "How much notice we give and where the change is published.",
      },
    ],
  },
  {
    slug: "complaints",
    title: "Complaints policy",
    sections: [
      { heading: "How to complain", covers: "In the app, by phone, by email or in writing." },
      {
        heading: "What we need from you",
        covers: "Your account number, what happened, and what you would like us to do.",
      },
      {
        heading: "Our timelines",
        covers:
          "When we acknowledge a complaint and how long we take to resolve it.",
      },
      {
        heading: "If you are not satisfied",
        covers: "Where to take the complaint next.",
      },
    ],
  },
  {
    slug: "cookies",
    title: "Cookies",
    sections: [
      {
        heading: "What this website stores",
        covers: "Which cookies or similar storage this website uses, if any, and why.",
      },
      {
        heading: "What the app stores",
        covers: "What the client app keeps on your device to keep you signed in.",
      },
      { heading: "Your choices", covers: "How to clear or refuse them and what stops working." },
    ],
  },
];
