/**
 * The Overview's figures for the day as a CSV file. Only totals the reader's role already sees
 * on screen: no client, account or person, so it needs no audit record of its own. Amounts
 * are whole numbers in the smallest unit.
 */

import type { Figures } from "./figures.ts";

type Line = readonly [section: string, item: string, value: string | number];

function quote(cell: string | number): string {
  const text = String(cell);
  // Product names are staff-set text: quote them, and stop a spreadsheet reading a formula.
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return /[",\r\n]/.test(safe) || safe !== text ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export function figuresCsv(figures: Figures): string {
  const lines: Line[] = [];
  const { money, floats, maturities, holdings } = figures;
  if (money) {
    lines.push(["Client money", "Held", money.clientMoneyHeld.minor]);
    lines.push(["Client money", "In transit", money.inTransit.minor]);
    lines.push(["Client money", "Paid in, waiting to start", money.awaitingStart.minor]);
    lines.push(["Clients", "Invested", money.clientsInvested]);
    lines.push(["Clients", "Verified", money.clientsVerified]);
    for (const day of money.days) {
      lines.push([`Money in, ${day.day}`, "In", day.moneyIn.minor]);
      lines.push([`Money out, ${day.day}`, "Out", day.moneyOut.minor]);
    }
  }
  for (const float of floats ?? []) lines.push(["Mobile money float", float.network.toUpperCase(), float.balance.minor]);
  for (const week of maturities ?? []) {
    lines.push([`Maturities ${week.firstDay} to ${week.lastDay}`, "Principal", week.principal.minor]);
    lines.push([`Maturities ${week.firstDay} to ${week.lastDay}`, "To pay out", week.payOutPrincipal.minor]);
  }
  for (const holding of holdings ?? []) lines.push(["Where it sits", holding.productName, holding.principal.minor]);
  const rows = [["Section", "Item", `Value (${figures.currency}, smallest unit, or a count)`], ...lines];
  return `${rows.map((row) => row.map(quote).join(",")).join("\r\n")}\r\n`;
}

export function figuresFilename(figures: Figures): string {
  return `finvest-overview-${figures.currency.toLowerCase()}-${figures.today}.csv`;
}
