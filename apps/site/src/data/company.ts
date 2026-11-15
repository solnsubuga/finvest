/**
 * Facts about Finvest that the pages state in more than one place.
 *
 * The first group are service promises chosen for now (docs/decisions.md). The second group
 * are facts only Finvest can supply. While one is null, the pages leave its line out. They
 * never show a placeholder and never guess. Fill one in here and the line appears wherever
 * it belongs.
 */
export interface TeamMember {
  name: string;
  /** Their job, such as "Chief Executive". */
  role: string;
}

/** Where clients and visitors write to us. */
export const supportEmail = "support@finvestug.com";

export const company = {
  /** How long a person takes to review an identity check. */
  reviewTime: "Usually within one working day",
  /** Working days within which a complaint should be resolved. */
  complaintWorkingDays: 10,
  city: "Kampala, Uganda",

  /** Support line, written the way it should be read, such as "+256 700 000 000". */
  phone: null,
  /** Street address of the office, without the city. */
  officeAddress: null,
  /** The registered company behind the trading name. */
  registeredName: null,
  /** Its company number at the Uganda Registration Services Bureau. */
  registrationNumber: null,
  /** The regulator's name, as it should be written. */
  regulator: null,
  licenceNumber: null,
  /** Its registration number with the Personal Data Protection Office. */
  dataProtectionRegistration: null,
  /** The bank that keeps the bills. */
  custodian: null,
  /** The firm that audits the accounts each year. */
  auditor: null,

  /**
   * The people named on About under "The people responsible for your money", in the order
   * shown. The section is left out while the list is empty.
   */
  team: [],
  /** The address people write to about working at Finvest. The footer's Careers link needs it. */
  careersEmail: null,
  /** The client app's App Store page, on apps.apple.com. */
  appStoreUrl: null,
  /** The client app's Google Play page, on play.google.com. */
  playStoreUrl: null,
} as {
  reviewTime: string;
  complaintWorkingDays: number;
  city: string;
  phone: string | null;
  officeAddress: string | null;
  registeredName: string | null;
  registrationNumber: string | null;
  regulator: string | null;
  licenceNumber: string | null;
  dataProtectionRegistration: string | null;
  custodian: string | null;
  auditor: string | null;
  team: TeamMember[];
  careersEmail: string | null;
  appStoreUrl: string | null;
  playStoreUrl: string | null;
};
