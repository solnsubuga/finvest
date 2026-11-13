import { can, type ScreenPermission, type StaffSession } from "@/auth/types.ts";

export interface NavItem {
  /** URL path segment, and the key of the screen. */
  path: string;
  label: string;
  /** Page heading. Sentence case, as on the canvas. */
  title: string;
  /** One line under the page heading. */
  summary: string;
  permission: ScreenPermission;
  /** Roadmap phase in which the real screen ships. See docs/roadmap.md. */
  phase: 0 | 1 | 2 | 3 | 4;
}

export interface NavGroup {
  title: string;
  items: readonly NavItem[];
}

/** The console menu, in the order and grouping drawn on the canvas (AdminNav). */
export const NAV_GROUPS: readonly NavGroup[] = [
  {
    title: "Daily work",
    items: [
      {
        path: "overview",
        label: "Overview",
        title: "Overview",
        summary: "Client money, payments in progress and what needs attention today.",
        permission: "overview.view",
        phase: 2,
      },
      {
        path: "review-queue",
        label: "Review Queue",
        title: "Review queue",
        summary: "Only items the system could not clear on its own.",
        permission: "review_queue.view",
        phase: 1,
      },
      {
        path: "identity-checks",
        label: "Identity Checks",
        title: "Identity checks",
        summary: "ID documents and selfies that need a person to decide.",
        permission: "identity_checks.view",
        phase: 1,
      },
      {
        path: "maturities",
        label: "Maturities",
        title: "Maturities",
        summary: "Investments maturing soon, and the daily run that rolls them over or pays them out.",
        permission: "maturities.view",
        phase: 2,
      },
      {
        path: "support",
        label: "Support and Messages",
        title: "Support and messages",
        summary: "Conversations with clients.",
        permission: "support.view",
        phase: 1,
      },
      {
        path: "complaints",
        label: "Complaints",
        title: "Complaints",
        summary: "The regulated complaints register, kept apart from general support.",
        permission: "complaints.view",
        phase: 1,
      },
    ],
  },
  {
    title: "Clients and money",
    items: [
      {
        path: "clients",
        label: "Clients",
        title: "Clients",
        summary: "Every client, their accounts and their history.",
        permission: "clients.view",
        phase: 1,
      },
      {
        path: "money-movement",
        label: "Money Movement",
        title: "Money movement",
        summary: "Payments in and payouts, by mobile money and bank.",
        permission: "money_movement.view",
        phase: 1,
      },
      {
        path: "reconciliation",
        label: "Reconciliation",
        title: "Reconciliation",
        summary: "The ledger compared with provider and bank statements, and anything that does not match.",
        permission: "reconciliation.view",
        phase: 1,
      },
      {
        path: "investments",
        label: "Investments",
        title: "Investments",
        summary: "Client holdings, interest added each day, and maturity dates.",
        permission: "investments.view",
        phase: 2,
      },
      {
        path: "automatic-investing",
        label: "Automatic Investing",
        title: "Automatic investing",
        summary: "Plans that invest the same amount every week or month, and what each day's run did.",
        permission: "automatic_investing.view",
        phase: 4,
      },
      {
        path: "goals",
        label: "Goals",
        title: "Goals",
        summary: "What clients are saving towards, and the investments each goal is made of.",
        permission: "goals.view",
        phase: 4,
      },
      {
        path: "dealing-desk",
        label: "Dealing Desk",
        title: "Dealing desk",
        summary: "The daily position, what to buy, allocation to clients, and the liquidity buffer.",
        permission: "dealing_desk.view",
        phase: 2,
      },
      {
        path: "finance",
        label: "Finance",
        title: "Finance",
        summary: "Fee income, tax held, and the export to the accounting system.",
        permission: "finance.view",
        phase: 2,
      },
    ],
  },
  {
    title: "Catalogue",
    items: [
      {
        path: "products",
        label: "Products",
        title: "Products",
        summary: "What clients can invest in.",
        permission: "products.view",
        phase: 2,
      },
      {
        path: "rates",
        label: "Rates",
        title: "Rates",
        summary: "Rate changes, each proposed by one person and approved by another.",
        permission: "rates.view",
        phase: 2,
      },
    ],
  },
  {
    title: "Control",
    items: [
      {
        path: "compliance",
        label: "Compliance",
        title: "Compliance",
        summary: "Sanctions screening, transaction monitoring and reports to the regulator.",
        permission: "compliance.view",
        phase: 1,
      },
      {
        path: "team",
        label: "Team and Permissions",
        title: "Team and permissions",
        summary: "Who works here, what each role can do, and what is waiting for a second person.",
        permission: "team.view",
        phase: 0,
      },
      {
        path: "reports",
        label: "Reports and Audit",
        title: "Reports and audit",
        summary: "Reports from the ledger, and the record of who did what and why.",
        permission: "reports.view",
        phase: 0,
      },
      {
        path: "settings",
        label: "Settings",
        title: "Settings",
        summary: "The rules the platform runs on. Changes here need a second person.",
        permission: "settings.view",
        phase: 1,
      },
      {
        path: "migration",
        label: "Migration",
        title: "Migration",
        summary: "Moving Finvest 1.0 clients across.",
        permission: "migration.view",
        phase: 3,
      },
    ],
  },
];

export const NAV_ITEMS: readonly NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

/** The groups this person can see. A group with nothing in it is left out. */
export function visibleNav(session: StaffSession): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    title: group.title,
    items: group.items.filter((item) => can(session, item.permission)),
  })).filter((group) => group.items.length > 0);
}

/** Where to send someone after sign-in: the first screen they are allowed to open. */
export function homePath(session: StaffSession): string | null {
  const first = visibleNav(session)[0]?.items[0];
  return first ? `/${first.path}` : null;
}
