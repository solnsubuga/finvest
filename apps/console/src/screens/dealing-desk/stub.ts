import {
  DealingError,
  type Amount,
  type BondPosition,
  type Cover,
  type DealingApi,
  type Desk,
  type Fill,
  type NewOrder,
  type Order,
  type OrderAction,
  type ProductPosition,
} from "./api.ts";

/**
 * A sample desk for `pnpm dev` and previews, with the figures drawn on the
 * canvas. Not real money and not connected to Finvest. Commands change only
 * this browser tab's copy. The arithmetic here stands in for the backend. The
 * screen itself never does any.
 */

const M = 1_000_000;
const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });
const usd = (cents: number): Amount => ({ minor: cents, currency: "USD" });

type Holding = Omit<ProductPosition, "toBuy" | "onOrder" | "cover">;
type BondHolding = Omit<BondPosition, "toBuy" | "onOrder" | "toSell" | "onSale" | "cover" | "toPlace">;
type StoredOrder = Omit<Order, "allowed"> & { preparedById: string; recordedById: string | null };

function sampleHoldings(): Holding[] {
  return [
    {
      productCode: "TREA-91",
      productName: "91 Day Treasury Bill",
      discountOnly: true,
      lotSize: ugx(100_000),
      clientsOwed: ugx(7_412 * M),
      billsHeld: ugx(7_180 * M),
      faceHeld: ugx(7_400 * M),
      nextAuction: "2026-11-18",
    },
    {
      productCode: "TREA-182",
      productName: "182 Day Treasury Bill",
      discountOnly: true,
      lotSize: ugx(100_000),
      clientsOwed: ugx(9_870 * M),
      billsHeld: ugx(9_716 * M),
      faceHeld: ugx(10_350 * M),
      nextAuction: "2026-11-18",
    },
    {
      productCode: "USD-BOND",
      productName: "USD Fixed Income",
      discountOnly: false,
      lotSize: usd(1_000_00),
      clientsOwed: usd(362_000_00),
      billsHeld: usd(362_000_00),
      faceHeld: usd(360_000_00),
      nextAuction: null,
    },
  ];
}

function sampleBonds(): BondHolding[] {
  const issue = (code: string, isin: string, name: string, maturityDate: string, holders: number, figures: number[]): BondHolding => {
    const [clientsFace = 0, clientsPaid = 0, faceHeld = 0, costHeld = 0] = figures;
    return {
      code,
      isin,
      name,
      maturityDate,
      holders,
      clientsFace: ugx(clientsFace),
      clientsPaid: ugx(clientsPaid),
      faceHeld: ugx(faceHeld),
      costHeld: ugx(costHeld),
      lotSize: ugx(100_000),
    };
  };
  return [
    // Clients bought more this week than the desk holds, and one bid is waiting.
    issue("BOND-2Y-2028", "UG0000001376", "2 Year Treasury Bond 2028", "2028-11-16", 14, [186 * M, 183_600_000, 150 * M, 148_100_000]),
    issue("BOND-5Y-2032", "UG12J2005328", "5 Year Treasury Bond 2032", "2032-05-20", 9, [80 * M, 76_400_000, 80 * M, 76_350_000]),
    // A client sold 1m early, so the desk holds more than clients do.
    issue("BOND-10Y-2037", "UG12J0711371", "10 Year Treasury Bond 2037", "2037-02-11", 6, [54 * M, 53_200_000, 55 * M, 54_100_000]),
  ];
}

function sampleOrders(): StoredOrder[] {
  const order = (id: string, productCode: string, productName: string, face: number): StoredOrder => ({
    id,
    kind: "auction_bid",
    instrument: "bill",
    productCode,
    productName,
    face: ugx(face),
    status: "prepared",
    preparedBy: "Daniel",
    preparedById: "sample-daniel",
    preparedAt: "2026-11-13T11:20:00Z",
    note: null,
    result: null,
    resultRecordedBy: null,
    recordedById: null,
  });
  return [
    order("-OdSampleOrder00001A", "TREA-91", "91 Day Treasury Bill", 240 * M),
    order("-OdSampleOrder00002A", "TREA-182", "182 Day Treasury Bill", 160 * M),
    {
      // Bought yesterday. Grace entered the custodian's note, so anyone else can confirm it.
      ...order("-OdSampleOrder00003A", "TREA-182", "182 Day Treasury Bill", 50 * M),
      kind: "secondary_purchase",
      status: "result_recorded",
      preparedAt: "2026-11-12T07:05:00Z",
      result: {
        face: ugx(50 * M),
        amount: ugx(46_900_000),
        settledOn: "2026-11-12",
        maturesOn: "2027-05-13",
        custodianReference: "CSD-2026-0481",
      },
      resultRecordedBy: "Grace",
      recordedById: "sample-grace",
    },
    {
      ...order("-OdSampleOrder00004A", "BOND-2Y-2028", "2 Year Treasury Bond 2028", 30 * M),
      instrument: "bond",
      preparedAt: "2026-11-13T09:40:00Z",
    },
  ];
}

const OPEN = ["prepared", "approved", "result_recorded"];

export interface StubStaff {
  id: string;
  name: string;
}

export function createStubDealingApi(
  me: StubStaff,
  delayMs = 150,
  today = () => new Date().toISOString().slice(0, 10),
  /** "no_bonds" shows the desk before any bond issue is open. */
  sample: "sample" | "no_bonds" = "sample",
): DealingApi {
  const holdings = sampleHoldings();
  const bonds = sample === "no_bonds" ? [] : sampleBonds();
  const orders = sampleOrders().filter((order) => bonds.length > 0 || order.instrument === "bill");
  const replies = new Map<string, Desk>();
  // The trust account holds the money waiting for bonds as well as the buffer.
  let cash = ugx(528 * M + bondPositions(bonds, orders).reduce((total, bond) => total + bond.toPlace.minor, 0));
  let created = 0;

  function allowed(order: StoredOrder): OrderAction[] {
    if (order.status === "prepared") return order.preparedById === me.id ? ["withdraw"] : ["approve", "send_back"];
    if (order.status === "approved") return ["record_result"];
    if (order.status === "result_recorded") return order.recordedById === me.id ? [] : ["confirm_result", "reject_result"];
    return [];
  }

  function toOrder(stored: StoredOrder): Order {
    const { id, kind, instrument, productCode, productName, face, status, preparedBy, preparedAt, note, result, resultRecordedBy } = stored;
    return {
      id,
      kind,
      instrument,
      productCode,
      productName,
      face,
      status,
      preparedBy,
      preparedAt,
      note,
      result,
      resultRecordedBy,
      allowed: allowed(stored),
    };
  }

  function desk(): Desk {
    const open = orders.filter((order) => OPEN.includes(order.status));
    const positions = holdings.map((holding): ProductPosition => {
      const zero = { minor: 0, currency: holding.clientsOwed.currency };
      const gap = holding.clientsOwed.minor - holding.billsHeld.minor;
      const onOrder = open
        .filter((order) => order.instrument === "bill" && order.productCode === holding.productCode && order.kind !== "secondary_sale")
        .reduce((total, order) => total + order.face.minor, 0);
      const cover: Cover = gap < 0 ? "surplus" : gap === 0 ? "covered" : onOrder >= gap ? "on_order" : "bid_needed";
      return { ...holding, toBuy: { ...zero, minor: Math.max(gap, 0) }, onOrder: { ...zero, minor: onOrder }, cover };
    });
    const shillings = positions.filter((position) => position.clientsOwed.currency === "UGX");
    const bondLines = bondPositions(bonds, orders);
    const toPlace =
      shillings.reduce((total, position) => total + position.toBuy.minor, 0) + bondLines.reduce((total, bond) => total + bond.toPlace.minor, 0);
    const balance = Math.max(cash.minor - toPlace, 0);
    const asked = 23_500_000;
    return {
      moneyToPlace: ugx(toPlace),
      billsHeld: ugx(shillings.reduce((total, position) => total + position.billsHeld.minor, 0) + 1_344 * M),
      matchesCustodian: true,
      buffer: {
        balance: ugx(balance),
        target: ugx(150 * M),
        floor: ugx(100 * M),
        level: balance < 100 * M ? "below_floor" : balance < 150 * M ? "under_target" : "on_target",
        fromBufferUpTo: ugx(5 * M),
        payWithinWorkingDays: 2,
        afterPromised: ugx(Math.max(balance - asked, 0)),
      },
      earlyWithdrawals: { asked: ugx(asked), clients: 4 },
      positions,
      bondsHeld: ugx(bondLines.reduce((total, bond) => total + bond.costHeld.minor, 0)),
      bonds: bondLines,
      orders: open.map(toOrder),
    };
  }

  /** Runs a command once per key, as the backend's idempotency keys do. */
  function command(key: string, run: () => void): Promise<Desk> {
    return new Promise((resolve, reject) =>
      setTimeout(() => {
        const saved = replies.get(key);
        if (saved) return resolve(saved);
        try {
          run();
          const reply = desk();
          replies.set(key, reply);
          resolve(reply);
        } catch (error) {
          reject(error instanceof Error ? error : new DealingError("unavailable"));
        }
      }, delayMs),
    );
  }

  function find(orderId: string, action: OrderAction): StoredOrder {
    const order = orders.find((candidate) => candidate.id === orderId);
    if (!order) throw new DealingError("not_found");
    if (!allowed(order).includes(action)) {
      const second = action === "approve" || action === "confirm_result";
      throw new DealingError(second && OPEN.includes(order.status) ? "same_person" : "conflict");
    }
    return order;
  }

  function holdingFor(productCode: string): Holding {
    const holding = holdings.find((candidate) => candidate.productCode === productCode);
    if (!holding) throw new DealingError("not_found");
    return holding;
  }

  function prepareBill(input: NewOrder) {
    const holding = holdingFor(input.productCode);
    if (input.face.minor <= 0 || input.face.minor % holding.lotSize.minor !== 0) throw new DealingError("not_a_whole_lot");
    if (input.kind === "secondary_sale" && input.face.minor > holding.faceHeld.minor) throw new DealingError("more_than_held");
    return { productCode: holding.productCode, productName: holding.productName };
  }

  function bondFor(code: string): BondHolding {
    const bond = bonds.find((candidate) => candidate.code === code);
    if (!bond) throw new DealingError("not_found");
    return bond;
  }

  function settle(order: StoredOrder, fill: Fill) {
    if (order.instrument === "bond") return settleBond(order, fill);
    const holding = holdingFor(order.productCode);
    const buys = order.kind !== "secondary_sale";
    if (buys) {
      holding.billsHeld = { ...holding.billsHeld, minor: holding.billsHeld.minor + fill.amount.minor };
      holding.faceHeld = { ...holding.faceHeld, minor: holding.faceHeld.minor + fill.face.minor };
    } else {
      const released = Math.floor((holding.billsHeld.minor * fill.face.minor) / holding.faceHeld.minor);
      holding.billsHeld = { ...holding.billsHeld, minor: holding.billsHeld.minor - released };
      holding.faceHeld = { ...holding.faceHeld, minor: holding.faceHeld.minor - fill.face.minor };
    }
    if (fill.amount.currency === "UGX") cash = ugx(cash.minor + (buys ? -fill.amount.minor : fill.amount.minor));
  }

  function settleBond(order: StoredOrder, fill: Fill) {
    const bond = bondFor(order.productCode);
    const buys = order.kind !== "secondary_sale";
    const released = buys ? 0 : Math.floor((bond.costHeld.minor * fill.face.minor) / bond.faceHeld.minor);
    bond.faceHeld = ugx(bond.faceHeld.minor + (buys ? fill.face.minor : -fill.face.minor));
    bond.costHeld = ugx(bond.costHeld.minor + (buys ? fill.amount.minor : -released));
    cash = ugx(cash.minor + (buys ? -fill.amount.minor : fill.amount.minor));
  }

  function prepareBond(input: NewOrder) {
    const bond = bondFor(input.productCode);
    if (input.face.minor <= 0 || input.face.minor % bond.lotSize.minor !== 0) throw new DealingError("not_a_whole_lot");
    const line = bondPositions([bond], orders)[0];
    if (input.kind === "secondary_sale" && line && input.face.minor > bond.faceHeld.minor - line.onSale.minor) throw new DealingError("more_than_held");
    return { productCode: bond.code, productName: bond.name };
  }

  return {
    getDesk: () => new Promise((resolve) => setTimeout(() => resolve(desk()), delayMs)),
    prepareOrder: (key, input) =>
      command(key, () => {
        const named = input.instrument === "bond" ? prepareBond(input) : prepareBill(input);
        created += 1;
        orders.push({
          id: `-OdSampleOrderNew${String(created).padStart(3, "0")}`,
          kind: input.kind,
          instrument: input.instrument,
          ...named,
          face: input.face,
          status: "prepared",
          preparedBy: me.name,
          preparedById: me.id,
          preparedAt: new Date().toISOString(),
          note: null,
          result: null,
          resultRecordedBy: null,
          recordedById: null,
        });
      }),
    approve: (key, orderId) => command(key, () => void (find(orderId, "approve").status = "approved")),
    sendBack: (key, orderId, note) => command(key, () => Object.assign(find(orderId, "send_back"), { status: "sent_back", note })),
    withdraw: (key, orderId) => command(key, () => void (find(orderId, "withdraw").status = "withdrawn")),
    recordResult: (key, orderId, fill) =>
      command(key, () => {
        const order = find(orderId, "record_result");
        if (fill === null) return void (order.status = "unsuccessful");
        if (fill.face.minor > order.face.minor) throw new DealingError("over_the_order");
        if (order.instrument === "bill" && holdingFor(order.productCode).discountOnly && fill.amount.minor > fill.face.minor)
          throw new DealingError("above_face");
        if (order.instrument === "bond" && fill.maturesOn !== bondFor(order.productCode).maturityDate) throw new DealingError("wrong_maturity");
        if (fill.settledOn > today()) throw new DealingError("settles_in_the_future");
        Object.assign(order, { status: "result_recorded", result: fill, resultRecordedBy: me.name, recordedById: me.id, note: null });
      }),
    confirmResult: (key, orderId) =>
      command(key, () => {
        const order = find(orderId, "confirm_result");
        if (order.result) settle(order, order.result);
        order.status = "settled";
      }),
    rejectResult: (key, orderId, note) =>
      command(key, () =>
        Object.assign(find(orderId, "reject_result"), {
          status: "approved",
          result: null,
          resultRecordedBy: null,
          recordedById: null,
          note,
        }),
      ),
  };
}

/** Each issue's position, as the backend's domain/bond_position.py works it out. */
function bondPositions(bonds: readonly BondHolding[], orders: readonly StoredOrder[]): BondPosition[] {
  return bonds.map((bond) => {
    const open = orders.filter((order) => order.instrument === "bond" && order.productCode === bond.code && OPEN.includes(order.status));
    const sum = (sells: boolean) =>
      open.filter((order) => (order.kind === "secondary_sale") === sells).reduce((total, order) => total + order.face.minor, 0);
    const onOrder = sum(false);
    const onSale = sum(true);
    const toBuy = Math.max(bond.clientsFace.minor - bond.faceHeld.minor, 0);
    const surplus = Math.max(bond.faceHeld.minor - bond.clientsFace.minor, 0);
    const cover: Cover = surplus > 0 ? "surplus" : toBuy === 0 ? "covered" : toBuy > onOrder ? "bid_needed" : "on_order";
    // What clients paid for the face value with no bond behind it, rounded up.
    const share = BigInt(bond.clientsPaid.minor) * BigInt(toBuy);
    const face = BigInt(bond.clientsFace.minor);
    const toPlace = toBuy === 0 ? 0n : (share + face - 1n) / face;
    return {
      ...bond,
      toBuy: ugx(toBuy),
      onOrder: ugx(onOrder),
      toSell: ugx(Math.max(surplus - onSale, 0)),
      onSale: ugx(onSale),
      cover,
      toPlace: ugx(Number(toPlace)),
    };
  });
}
