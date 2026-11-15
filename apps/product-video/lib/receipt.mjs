// The invented bank's receipt for a transfer, drawn for the person who made it, plainly marked
// as a specimen. Only the bank's side is drawn here: the amount, the day, the receipt number and
// the payment reference the app gave the client, read from the screen.
import path from "node:path";
import { chromium } from "playwright";

export async function drawReceipt(file, { bank, payer, toName, toNumber, amount, day, number, reference }) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 900, height: 1200 } });
  const row = (k, v) => `<div style="display:flex;justify-content:space-between;padding:14px 0;border-bottom:2px dashed #ccc"><span style="color:#555">${k}</span><b>${v}</b></div>`;
  await page.setContent(`<!doctype html><body style="margin:0;background:#3b3b3b;display:grid;place-items:center;height:1200px;font:30px 'DejaVu Sans',sans-serif">
    <div style="width:760px;background:#fbfaf5;padding:50px 56px;box-shadow:0 20px 50px rgba(0,0,0,.5);transform:rotate(1deg);position:relative;overflow:hidden">
      <div style="font-size:42px;font-weight:800;color:#1f3d63">${bank}</div>
      <div style="color:#555;margin:6px 0 30px">Funds transfer receipt</div>
      ${row("From", payer)}${row("To", toName)}${row("Account", toNumber)}${row("Amount", amount)}${row("Date", day)}${row("Receipt no.", number)}${row("Reference", reference)}
      <div style="margin-top:34px;font-size:22px;color:#777">Invented for the Finvest product video. Not a real receipt.</div>
      <div style="position:absolute;right:-40px;top:420px;transform:rotate(-28deg);font-size:90px;font-weight:800;color:rgba(180,35,24,.22);letter-spacing:12px">SPECIMEN</div>
    </div></body>`);
  await page.screenshot({ path: file, type: "jpeg", quality: 90 });
  await browser.close();
  return path.resolve(file);
}
