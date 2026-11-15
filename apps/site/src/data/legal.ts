/**
 * The legal documents, written out in full.
 *
 * The wording is a draft written to match how Finvest works today (docs/). It must be reviewed
 * by Finvest's lawyers before launch: until a document's `reviewedBy` is set, its page and PDF
 * say it is a draft, and `pnpm placeholders --strict` fails.
 *
 * Facts only Finvest can supply come from src/data/company.ts. While one is empty, the sentence
 * that needs it is left out. Nothing is guessed and nothing is bracketed.
 */

import { company, supportEmail } from "./company.ts";

/** The notice on a document that shows only its structure (the earliest versions). */
export const layoutOnlyNotice =
  "The headings below show the structure. The wording must come from Finvest's lawyers before launch.";

/** The notice on a document whose wording has not yet been reviewed by Finvest's lawyers. */
export const draftNotice =
  "This wording describes how Finvest works and is waiting for review by Finvest's lawyers. It may change before launch.";

/** A paragraph, or a list of short points. */
export type Block = string | { list: string[] };

export interface LegalSection {
  heading: string;
  body: Block[];
}

export interface LegalDocument {
  slug: string;
  title: string;
  /**
   * For the documents a client agrees to (the terms and the privacy notice): the day this
   * wording took effect, as YYYY-MM-DD. The API records it when a client signs up or claims an
   * account (`backend/app/modules/identity/domain/terms.py`, which a backend test keeps equal
   * to this), and the app links to this version's PDF at /legal/<slug>-<version>.pdf.
   */
  version?: string;
  /**
   * "layout": headings only. "draft": written out, waiting for the lawyers. "reviewed": the
   * lawyers have approved this wording; `reviewedBy` says who and when.
   */
  stage: "layout" | "draft" | "reviewed";
  reviewedBy?: string;
  /** One line for the page's description. */
  summary: string;
  sections: LegalSection[];
}

/** "14 November 2026" for a YYYY-MM-DD version. */
export function versionLabel(version: string): string {
  const [year, month, day] = version.split("-").map(Number);
  const months = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  return `${day} ${months[(month ?? 1) - 1]} ${year}`;
}

/** Keeps the sentences whose facts are known. */
function known(...sentences: (string | false | null | undefined)[]): string {
  return sentences.filter((sentence): sentence is string => Boolean(sentence)).join(" ");
}

/** Drops empty paragraphs, so a paragraph built only from missing facts disappears. */
function blocks(...items: (Block | false | null | undefined)[]): Block[] {
  return items.filter((item): item is Block => Boolean(item) && item !== "");
}

const we = company.registeredName ?? "Finvest";
const office = company.officeAddress ? `${company.officeAddress}, ${company.city}` : null;

const whoWeAre = known(
  company.registeredName &&
    `Finvest is a trading name of ${company.registeredName}${
      company.registrationNumber ? `, registered in Uganda under company number ${company.registrationNumber}` : ""
    }.`,
  company.regulator &&
    `${company.registeredName ?? "Finvest"} is regulated by ${company.regulator}${
      company.licenceNumber ? ` under licence ${company.licenceNumber}` : ""
    }.`,
  office && `Our office is at ${office}.`,
);

const howToReachUs = known(
  `You can reach us through the chat in the app, every day from 8am to 8pm, or by email at ${supportEmail}.`,
  company.phone && `You can call us on ${company.phone}, Monday to Friday, 8am to 6pm.`,
  office && `You can write to us at ${office}.`,
);

/**
 * Earlier versions of the terms and the privacy notice, kept so the PDF a client agreed to
 * stays at /legal/<slug>-<version>.pdf for good. When a document changes, move a copy of its
 * old entry here, unchanged, and give the new entry its new version.
 */
export const earlierVersions: LegalDocument[] = [
  {
    slug: "terms",
    title: "Terms and conditions",
    version: "2026-11-14",
    stage: "layout",
    summary: "Terms and conditions for Finvest clients: the structure only.",
    sections: [
      { heading: "Who we are", body: ["The company behind Finvest, who regulates it, and its licence."] },
      {
        heading: "Opening an account",
        body: ["Who can open an account, the identity checks we must carry out, and how your account number is issued."],
      },
      {
        heading: "Investing",
        body: ["How orders are placed, when an investment starts, how rates are set, and how tax is deducted."],
      },
      {
        heading: "Payments and payouts",
        body: [
          "Accepted payment methods, payout accounts in your own name, limits, and what happens when a payment fails.",
        ],
      },
      {
        heading: "Early withdrawal",
        body: [
          "How an investment is ended before maturity, and why the amount can differ from the amount at maturity.",
        ],
      },
      { heading: "Fees", body: ["The fees that apply, shown before you confirm any action."] },
      {
        heading: "Keeping your account safe",
        body: ["Your responsibilities for your password, passkey, PIN and devices, and ours."],
      },
      { heading: "Complaints and disputes", body: ["How to complain, our timelines, and where to take it next."] },
      {
        heading: "Closing your account",
        body: ["How to close once nothing is left invested or owed to you, and the records we keep afterwards."],
      },
    ],
  },
  {
    slug: "privacy",
    title: "Privacy notice",
    version: "2026-11-14",
    stage: "layout",
    summary: "The privacy notice for Finvest clients: the structure only.",
    sections: [
      {
        heading: "Who is responsible for your data",
        body: [
          "The company responsible for your data as its controller, its registration under the Data Protection and Privacy Act, 2019, and how to reach its data protection officer.",
        ],
      },
      {
        heading: "What we collect",
        body: [
          "Identity documents and selfies, contact details, the next of kin you name, your answers to the investing questions, payout accounts, payments and investments, and device and sign-in records.",
        ],
      },
      {
        heading: "Why we use it",
        body: ["To check who you are, run your investments, meet our legal duties, and keep your account safe."],
      },
      {
        heading: "Who we share it with",
        body: [
          "Payment providers, the custodian, identity check providers, regulators and tax authorities, and the next of kin you named if we cannot reach you. Nobody else.",
        ],
      },
      {
        heading: "Where it is kept and for how long",
        body: [
          "Where data is stored, how it is protected, and how long each kind is kept, including after you close your account.",
        ],
      },
      {
        heading: "Your rights",
        body: ["How to download a copy of your data from Profile, correct it or object to its use, and how to complain."],
      },
    ],
  },
];

export const legalDocuments: LegalDocument[] = [
  {
    slug: "terms",
    title: "Terms and conditions",
    version: "2026-11-15",
    stage: "draft",
    summary:
      "The terms for a Finvest account: opening one, investing, paying in, payouts, early withdrawal, fees, security, complaints and closing.",
    sections: [
      {
        heading: "Who we are",
        body: blocks(
          whoWeAre,
          `In these terms "we", "us" and "Finvest" mean ${we}. "You" means the person, or the group or company, whose account it is.`,
          "These terms, with the privacy notice, the risk disclosure, the fees and charges and the complaints policy on this website, are the agreement between you and us. You accept them when you open or claim your account, and the app records which version you accepted.",
        ),
      },
      {
        heading: "What Finvest does",
        body: [
          "Finvest lets you invest in Government of Uganda treasury bills, and in the other products shown in the app, in small amounts from your phone. You choose a product and an amount, pay for it by mobile money or bank transfer, and are paid back to an account in your own name.",
          "Finvest does not hold a balance for you to spend. There is no wallet. Every payment you make is for one investment or one saving towards a goal, and every payout goes straight to your own mobile money number or bank account.",
          "We do not give investment advice. The information in the app and on this website helps you decide, but the choice is yours.",
        ],
      },
      {
        heading: "Opening an account",
        body: [
          "You must be 18 or older. You open an account with your mobile number and a password, then hand in an identity check: a photo of your National ID, passport, driving permit or refugee ID, and a selfie. You confirm the details read from the document, and we may check them with the issuer and with identity check providers.",
          "The law requires us to know who our clients are and to check them against sanctions lists and lists of politically exposed persons, when you join and from time to time after. A person at Finvest reviews every identity check. We may ask you for more photos or information, and we may refuse an account without giving every reason where the law does not allow us to.",
          "When your check is approved you are given a 10 digit account number. It is yours for good and is never given to anyone else.",
          "We also ask a few questions about your investing. Your answers can limit the products or terms offered to you, so please answer them truthfully and update them when your situation changes.",
          "A group, SACCO or company account is opened by a person who already has their own approved account, and is checked as an organisation before it can invest. The people who sign for it approve withdrawals together, as set up in the app.",
        ],
      },
      {
        heading: "Making an investment",
        body: [
          "Before you pay, the app shows the product, the amount, the yearly rate after tax, the start and maturity dates, and the exact amount you will get back at maturity. When you confirm, the investment is saved and waits for your payment. If it is not paid within 72 hours it is cancelled, and nothing is owed. You can have at most five investments waiting for payment at a time.",
          "The investment starts on the day your payment is confirmed. Each product has a smallest amount, and one payment must meet it on its own.",
          "Rates follow the Government's auctions and change from time to time. The rate on your investment is the one in force on the day it was saved, and it does not change during its term.",
          "Interest is worked out every day on the amount invested, at the yearly rate, over a 365 day year. Nothing is earned on the start date. Interest is earned from the next day up to and including the maturity date.",
          "Tax that the law requires on interest is taken off before interest reaches you, and we pay it to the Uganda Revenue Authority. The app always shows interest after tax. Each year you can download a certificate of the tax withheld.",
          "A member of our staff may book an investment for you at your request. You see it in the app and pay for it there, exactly as if you had made it yourself.",
        ],
      },
      {
        heading: "When an investment matures",
        body: [
          "You choose what happens at maturity, at any time up to the day before:",
          {
            list: [
              "Roll over: the amount and its interest after tax are invested again in the same product, at the rate in force on the maturity date. This is what happens if you do not choose.",
              "Pay out: the amount and its interest are paid to one of your own payout accounts.",
              "Pay out the interest: the interest is paid to you, and the amount you invested is invested again.",
              "Save the interest to a goal: the interest starts a saving towards one of your goals, and the amount you invested is invested again.",
            ],
          },
          "If the product is no longer taking money on the maturity date, nothing is invested without your say. The money waits, and we ask you what you would like to do with it.",
        ],
      },
      {
        heading: "Goals and automatic investing",
        body: [
          "A goal is a target, such as school fees, that your investments count towards. It holds no money of its own. Each saving towards a goal is an investment of its own, paid for like any other.",
          "An automatic investing plan invests the same amount on the days you choose. On each day we send a mobile money prompt for that amount to your own number, and no money leaves your account unless you approve the prompt with your mobile money PIN. You can pause or stop a plan at any time.",
        ],
      },
      {
        heading: "Paying in",
        body: [
          "You pay by MTN or Airtel mobile money, or by bank transfer from a bank account in your own name.",
          "For mobile money, you approve a prompt on your phone. For a bank transfer, you send the exact amount to the account the app shows, then upload the receipt. Two members of our staff check it against our bank statement before the investment starts.",
          "A payment for a different amount, from an account in another name, or that we cannot match to an investment, is not kept as credit. We return it, or with your agreement match it to the right investment. Money that is returned is paid as soon as it can be, with no fee.",
          "A mobile money payment can sometimes end without a clear answer from the network. We never assume what happened: we ask the provider, and an investment starts only once we know the money arrived.",
        ],
      },
      {
        heading: "Payout accounts and payouts",
        body: [
          "Money is only ever paid to a mobile money number or bank account in your own name. To add one you confirm a code we send you, and for a bank account you upload proof that it is yours. A person at Finvest checks it. A number or account whose name is not the name on your identity document is refused, and nobody at Finvest can override this. A new payout account can receive its first payout 24 hours after it is approved.",
          "Payouts have limits, which the app shows, and they can change. Today one mobile money payout is at most UGX 5,000,000. A large payout may be held for a check by two members of our staff before it is sent.",
          "After you sign in with a code sent by SMS or email, or reset your password, withdrawals can only go to payout accounts you already had, for 24 hours. This protects you if someone else gets hold of your phone or SIM card.",
          "If a payout fails, the money is kept for you and we pay it again or contact you. It is never added to a balance.",
        ],
      },
      {
        heading: "Early withdrawal",
        body: [
          "Where the app offers it, you can ask to end an investment before it matures, once, up to the day before its maturity date. The app shows the exact amount before you confirm.",
          "Today an early withdrawal pays back the amount you invested plus the interest earned after tax up to the day you ask, with no fee. Interest stops on that day, so you do not receive the interest you would have earned up to maturity. Most withdrawals are paid at once; a large one can take up to two working days while we sell the bills behind it.",
          "We may change how early withdrawals are priced. We will tell you before a change applies, and the amount shown before you confirm is always the amount you are paid.",
        ],
      },
      {
        heading: "Sending an investment to another client",
        body: [
          "You can send all or part of a running investment to another approved Finvest client. Nothing is cashed out. The investment moves to them on the same rate and maturity date. Each part must meet the product's smallest amount. A large transfer may be held for a check by two members of our staff, and you can cancel it while it is held. A transfer that has been made cannot be undone by us, so check the recipient before you confirm.",
        ],
      },
      {
        heading: "Fees",
        body: [
          "The fees that apply are listed in our fees and charges, and the app shows any fee before you confirm. We give you at least 30 days' notice before a new fee or a higher fee applies.",
        ],
      },
      {
        heading: "Keeping your account safe",
        body: [
          "Keep your password, PIN, passkeys and any codes we send you to yourself. We will never ask you for them, by phone, SMS, email or chat. Signing in on a new device always needs your password and a second step: a passkey, an authenticator app, or a code by SMS or email. On your phone, your app PIN or fingerprint opens the app and confirms withdrawals and new payout accounts.",
          "If you lose your phone or think someone knows your password, freeze your account at once from Profile, then contact us. Nobody can sign in to a frozen account. To unfreeze it, two members of our staff check it is really you.",
          "Tell us straight away if your mobile number, email address or name changes, or if you see anything you did not do.",
          "We protect your account with encryption, the checks described here, and two-person approval for sensitive staff actions. We are responsible for losses caused by our own mistakes or failures, and the law may protect you further.",
        ],
      },
      {
        heading: "When we may pause or close an account",
        body: [
          "We may pause activity on an account, hold a payment or payout, or close an account if the law requires it, if a check on you or a payment raises a concern we must look into, if we suspect fraud, or if these terms are seriously broken. Where the law allows, we tell you why. Money that belongs to you is always paid to you, unless the law requires us to hold it.",
        ],
      },
      {
        heading: "Complaints and disputes",
        body: [
          "If something goes wrong, tell us. Our complaints policy on this website explains how, and what happens next. Making a complaint is free.",
          howToReachUs,
        ],
      },
      {
        heading: "Closing your account",
        body: [
          "You can ask to close your account from Profile once nothing is left: no running or unpaid investments, no money owed to you, no payment or payout in progress, no open goals and no active automatic investing plans. The app tells you what is left. Two members of our staff check the account is clear before it closes.",
          "When an account closes, its account number is never issued again, and the mobile number stays with it. We keep your records for as long as the law requires, as our privacy notice explains.",
        ],
      },
      {
        heading: "Changes to these terms",
        body: [
          "We may change these terms, for example when the law changes or we add a product. We tell you in the app and by email before a change applies, and give you at least 30 days' notice of a change that is not in your favour, unless the law requires it sooner. Each version is dated, and the version you accepted stays on this website as a PDF.",
          "If you do not accept a change, you can close your account, without charge, before it applies.",
        ],
      },
      {
        heading: "The law that applies",
        body: [
          "These terms are governed by the laws of Uganda, and the courts of Uganda decide any dispute about them. Nothing in these terms takes away rights the law gives you.",
        ],
      },
    ],
  },
  {
    slug: "privacy",
    title: "Privacy notice",
    version: "2026-11-15",
    stage: "draft",
    summary:
      "How Finvest collects, uses, shares and protects your personal data under Uganda's Data Protection and Privacy Act, 2019, and your rights.",
    sections: [
      {
        heading: "Who is responsible for your data",
        body: blocks(
          `${we} is responsible for your personal data as its controller, under Uganda's Data Protection and Privacy Act, 2019.`,
          company.dataProtectionRegistration &&
            `We are registered with the Personal Data Protection Office under number ${company.dataProtectionRegistration}.`,
          whoWeAre,
          `For anything about your data, write to ${supportEmail} and mark it for our data protection officer, or use the chat in the app.`,
        ),
      },
      {
        heading: "What we collect",
        body: [
          {
            list: [
              "Who you are: your names, date of birth, identity document and its number, your tax number if you give it, photos of your document, and the selfie or live face check you take.",
              "How to reach you: your mobile number, email address, and the next of kin you name. Next of kin are contacts only, and have no right to your investments.",
              "Your investing: your answers to the investing questions, your investments, goals and plans, and what you earn.",
              "Your money: the mobile money numbers and bank accounts you add, proof that they are yours, the name the network or bank holds for them, and every payment and payout.",
              "Your account and devices: how and when you sign in, the devices you use, your passkeys (we keep only a public key, never your fingerprint or face), and what you tell us in chat and complaints.",
            ],
          },
          "We collect most of this from you. Some comes from others: the issuer of your identity document and identity check providers, mobile networks and banks, sanctions and politically exposed person lists, and, for an account moved from paper records, the records we already held.",
        ],
      },
      {
        heading: "Why we use it",
        body: [
          {
            list: [
              "To open and run your account and investments, which we cannot do without it.",
              "To meet our legal duties: knowing who our clients are, screening for sanctions and politically exposed persons, watching for money laundering and fraud, tax reporting, keeping records, and answering regulators.",
              "To keep your account and money safe, for example by checking that a payout account is in your name.",
              "To tell you about your account: payments, maturities, changes to these documents, and anything we must tell you by law.",
            ],
          },
          "We do not sell your data, and we do not use it to show you advertising. If we want to send you news about new products, we ask you first, and you can stop at any time.",
          "Some checks are automatic. A payout account whose name does not match the name on your identity document is refused without a person deciding. If you think an automatic decision is wrong, tell us and a person will look at it.",
        ],
      },
      {
        heading: "Who we share it with",
        body: [
          "Only with those who need it, and only what they need:",
          {
            list: [
              "Mobile money and payment providers, and banks, to take payments and make payouts, and to check the name held for a number or account.",
              "Identity check and screening providers, to check your document and selfie and to screen you against sanctions and politically exposed person lists.",
              "Companies that send our SMS and email, and that host and run our systems.",
              "The custodian that keeps the government securities, and our auditors.",
              "Regulators, the Uganda Revenue Authority, the Financial Intelligence Authority, the police and courts, when the law requires it.",
              "Your next of kin, if we cannot reach you about your account.",
            ],
          },
          "Each provider may use your data only to do its work for us, and must protect it. We will send you a list of the providers we use if you ask.",
        ],
      },
      {
        heading: "Where it is kept",
        body: [
          "Our systems run on cloud servers in London, in the United Kingdom, and some of our providers handle data outside Uganda. We send personal data abroad only where the law allows it and the data is protected at least as well as Ugandan law requires.",
          "Identity details and documents are encrypted. Documents can only be opened through links that expire within minutes. Statements and receipts we email to you are locked with your date of birth. Our staff see only what their job needs, and what they do on an account is recorded in an audit trail that cannot be changed.",
        ],
      },
      {
        heading: "How long we keep it",
        body: [
          "We keep your data while your account is open, and after it closes for as long as the law requires us to keep records of who our clients are and of their transactions. Anti-money laundering law requires these records to be kept for at least ten years. When no law requires us to keep something, we delete it or make it anonymous.",
        ],
      },
      {
        heading: "Your rights",
        body: [
          {
            list: [
              "See your data: download a copy at any time from Profile, Language and data, or ask us for it.",
              "Correct it: change your contact details in the app, or ask us to correct anything else.",
              "Object, or ask us to stop or delete: we will, unless the law requires us to keep it or to keep using it.",
              "Withdraw your consent to news about products at any time.",
            ],
          },
          "We answer a request within 30 days. If you are not happy with how we handle your data, you can complain to the Personal Data Protection Office.",
        ],
      },
      {
        heading: "Changes to this notice",
        body: [
          "We tell you in the app and by email before a change applies. Each version is dated, and the version you accepted stays on this website as a PDF.",
        ],
      },
    ],
  },
  {
    slug: "risk",
    title: "Risk disclosure",
    stage: "draft",
    summary:
      "The risks of investing with Finvest: what treasury bills are, holding to maturity, early withdrawal, changing rates, dollar investments and what we do not promise.",
    sections: [
      {
        heading: "What you are buying",
        body: [
          "A treasury bill is a short loan to the Government of Uganda, sold by the Bank of Uganda at auction. You lend for a fixed term, such as 91 or 182 days, and are paid back with interest at maturity. Treasury bills are among the lowest-risk investments in Uganda, but no investment is free of risk.",
          "Other products in the app, such as fixed income in dollars, have their own terms, shown before you invest. Read them before you confirm.",
        ],
      },
      {
        heading: "Holding to maturity",
        body: [
          "If you hold a treasury bill to maturity, you are paid the amount shown when you invested. That depends on the Government of Uganda paying its debts, and on Finvest and the custodian keeping records correctly.",
        ],
      },
      {
        heading: "Your investments are not a bank deposit",
        body: [
          "An investment with Finvest is not a deposit with a bank, and it is not covered by the Deposit Protection Fund of Uganda.",
        ],
      },
      {
        heading: "Withdrawing early",
        body: [
          "If you take your money out before maturity, you do not receive the interest you would have earned up to maturity. The app shows the exact amount before you confirm. A large withdrawal can take up to two working days. Early withdrawal may not always be available, and how it is priced may change, as our terms explain.",
        ],
      },
      {
        heading: "Rates change",
        body: [
          "Rates are set by the Government's auctions and go up and down. A rate in force today is not a promise for tomorrow. When an investment rolls over, it earns the rate in force on the day it rolls over, which can be lower than before.",
        ],
      },
      {
        heading: "Inflation",
        body: [
          "If prices rise faster than your investment earns, your money buys less at maturity than it did when you invested.",
        ],
      },
      {
        heading: "Dollar investments",
        body: [
          "A dollar investment is paid back in dollars. If the shilling gets stronger against the dollar, it is worth fewer shillings at maturity than when you invested.",
        ],
      },
      {
        heading: "Tax can change",
        body: [
          "The tax on interest is set by law. If the law changes, what you earn after tax can change too.",
        ],
      },
      {
        heading: "What Finvest does not promise",
        body: [
          "Past rates are not a promise of future rates. Finvest gives no investment advice. Think about whether you may need the money before maturity, and do not invest money you cannot leave invested for the term.",
        ],
      },
    ],
  },
  {
    slug: "fees",
    title: "Fees and charges",
    stage: "draft",
    summary:
      "What Finvest charges: no fee to open an account, invest, roll over, be paid at maturity or withdraw early, and the charges others may make.",
    sections: [
      {
        heading: "What Finvest charges",
        body: [
          {
            list: [
              "Opening an account: no fee.",
              "Making an investment, by mobile money or bank transfer: no fee. You pay exactly the amount you invest.",
              "Rolling over at maturity: no fee.",
              "Being paid at maturity: no fee.",
              "Withdrawing early: no fee. The app shows the exact amount you are paid before you confirm.",
              "Sending an investment to another client: no fee.",
              "Statements, receipts and tax certificates: no fee.",
            ],
          },
          "The rate shown is what you earn, after tax.",
        ],
      },
      {
        heading: "Charges from your mobile money provider or bank",
        body: [
          "Your mobile network or bank may charge you for things Finvest does not control, such as sending a bank transfer to us or taking cash out from a mobile money agent. Ask them for their charges.",
        ],
      },
      {
        heading: "When fees change",
        body: [
          "If we ever add a fee or raise one, we publish it here and tell you in the app and by email at least 30 days before it applies. The app always shows any fee before you confirm.",
        ],
      },
    ],
  },
  {
    slug: "complaints",
    title: "Complaints policy",
    stage: "draft",
    summary:
      "How to make a complaint to Finvest, what we need from you, how quickly we answer, and where to take it next.",
    sections: [
      {
        heading: "How to complain",
        body: blocks(
          "Tell us in whatever way suits you. Making a complaint is free.",
          {
            list: [
              "In the app: open Help and choose Make a complaint. This is the fastest way, because we can see your account.",
              `By email: write to ${supportEmail}.`,
              ...(company.phone ? [`By phone: call ${company.phone}, Monday to Friday, 8am to 6pm.`] : []),
              ...(office ? [`In writing: send a letter to ${office}.`] : []),
            ],
          },
          "We never ask for your password, PIN or codes, so do not include them.",
        ),
      },
      {
        heading: "What we need from you",
        body: [
          "Your account number or mobile number, what happened and when, and what you would like us to do. Send any receipts or screenshots you have.",
        ],
      },
      {
        heading: "Our timelines",
        body: [
          `We acknowledge your complaint within one working day and tell you who is handling it. We aim to resolve it within ${company.complaintWorkingDays} working days. If we need longer, we tell you why and when you can expect an answer.`,
          "Every complaint is recorded in our complaints register, separate from everyday support chat, and reviewed by our compliance staff.",
        ],
      },
      {
        heading: "Our answer",
        body: [
          "We write to you with what we found, what we will do about it, and how to take it further if you are not satisfied. If we got something wrong, we put it right.",
        ],
      },
      {
        heading: "If you are not satisfied",
        body: blocks(
          company.regulator
            ? `If you are not happy with our answer, or we have not answered within ${company.complaintWorkingDays} working days, you can take your complaint to ${company.regulator}.`
            : `If you are not happy with our answer, or we have not answered within ${company.complaintWorkingDays} working days, you can take your complaint to the authority that regulates us, or to the courts.`,
          "A complaint about how we handle your personal data can also go to the Personal Data Protection Office.",
        ),
      },
    ],
  },
  {
    slug: "cookies",
    title: "Cookies",
    stage: "draft",
    summary:
      "What this website and the Finvest app store in your browser or on your phone, and why: only what is needed to keep you signed in and remember your settings.",
    sections: [
      {
        heading: "What this website stores",
        body: [
          "This website, finvestug.com, sets no cookies and stores nothing in your browser. It uses no analytics or advertising trackers, and it loads nothing from other websites.",
        ],
      },
      {
        heading: "What the app stores in a browser",
        body: [
          "The client app on the web, app.finvestug.com, stores only what it needs to work:",
          {
            list: [
              "A session cookie, finvest_client_session, that keeps you signed in. It cannot be read by scripts, is sent only to Finvest, and ends when you log out, after 10 minutes without use, or after 8 hours at most.",
              "Your data saving choices, kept in your browser's storage.",
              "While you open a group or company account, a note of that, so you return to the right step.",
            ],
          },
        ],
      },
      {
        heading: "What the app stores on your phone",
        body: [
          "On Android and iPhone, the app keeps your sign-in in your phone's secure storage (the Keychain or Keystore), with the key that lets your fingerprint or face open the app if you turn that on, and your data saving choices. Your fingerprint and face never leave your phone.",
        ],
      },
      {
        heading: "Your choices",
        body: [
          "All of these are needed for the app to work, so we do not ask for consent to them. You can clear them at any time in your browser's or phone's settings. If you do, you are logged out and your settings return to their defaults. If we ever add a cookie that is not needed, we will ask you first.",
        ],
      },
    ],
  },
];
