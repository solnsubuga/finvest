"""Design mockups for Pesapal payments: client app and console. Writes HTML per screen."""
import os, pathlib

OUT = pathlib.Path(__file__).parent / "html"
OUT.mkdir(exist_ok=True)

TOKENS = """
:root{--black:#0D0D0D;--lime:#B6EB0A;--limeBorder:#6E8F00;--limeSurface:#E9F7B5;--limeText:#2A3800;
--ink:#141414;--muted:#5E5E5A;--paper:#F4F5F0;--surface:#fff;--sunken:#ECECE8;--border:#E3E6DD;
--borderStrong:#C9CEC0;--divider:#EEF0EA;--neutral:#8C8C88;--warning:#B45309;--warningText:#8A4B00;
--warningSurface:#FDEFD8;--danger:#B42318;--dangerSurface:#FDECEA;--success:#1F7A3D;--successSurface:#E5F4EA;}
"""

CLIENT_CSS = TOKENS + """
@font-face{font-family:PJ;src:url(../../../../../backend/app/modules/documents/fonts/PlusJakartaSans-Regular.ttf);font-weight:400}
@font-face{font-family:PJ;src:url(../../../../../backend/app/modules/documents/fonts/PlusJakartaSans-Bold.ttf);font-weight:700}
@font-face{font-family:PJ;src:url(../../../../../backend/app/modules/documents/fonts/PlusJakartaSans-ExtraBold.ttf);font-weight:800}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:PJ,sans-serif;background:var(--paper);color:var(--ink);letter-spacing:-.01em}
.col{max-width:688px;margin:0 auto;padding:24px 24px 32px}
.wide .col{padding-top:48px}
.back{width:44px;height:44px;border-radius:50%;background:#fff;border:1px solid var(--border);display:flex;align-items:center;justify-content:center;margin-bottom:20px}
h1{font-size:30px;font-weight:800;letter-spacing:-.03em;line-height:1.12;margin-bottom:18px}
.wide h1{font-size:36px}
.hero{background:var(--black);color:#fff;border-radius:24px;padding:22px 20px;margin-bottom:22px}
.hero .p{color:#C9C9C4;font-size:14px}
.hero .amt{font-size:34px;font-weight:800;letter-spacing:-.03em;margin:6px 0 4px}
.wide .hero .amt{font-size:40px}
.label{font-size:12px;font-weight:700;letter-spacing:.08em;color:var(--muted);text-transform:uppercase;margin:4px 0 10px}
.opt{background:#fff;border:1px solid var(--border);border-radius:18px;padding:14px;margin-bottom:10px}
.opt.sel{border:2px solid var(--ink);padding:13px}
.opt.off{background:var(--sunken);border-color:var(--sunken)}
.opt .row{display:flex;align-items:center;gap:12px}
.badge{width:82px;flex:none;height:36px;border-radius:10px;background:var(--sunken);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;padding:0 8px;gap:4px}
.opt.off .badge{background:#E2E2DD;color:var(--neutral)}
.opt .t{font-weight:700;font-size:16px}
.opt .s{font-size:13px;color:var(--muted);margin-top:2px}
.opt.off .t{color:var(--neutral)}
.grow{flex:1}
.radio{width:24px;height:24px;border-radius:50%;border:2px solid var(--borderStrong)}
.check{width:26px;height:26px;border-radius:50%;background:var(--lime);display:flex;align-items:center;justify-content:center}
.chev{color:var(--muted);font-size:20px}
.inner{margin-top:14px;border-top:1px solid var(--divider);padding-top:12px}
.field{background:var(--sunken);border-radius:14px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;font-size:14px}
.field b{font-size:16px}
.note{font-size:13px;color:var(--muted);line-height:1.45;margin-top:10px}
.lines{border-radius:14px;background:var(--paper);padding:4px 14px}
.lines .l{display:flex;justify-content:space-between;padding:9px 0;font-size:14px;border-bottom:1px solid var(--divider)}
.lines .l:last-child{border-bottom:0;font-weight:700;font-size:15px}
.lines .l span:first-child{color:var(--muted)}
.lines .l:last-child span:first-child{color:var(--ink)}
.brands{display:flex;gap:6px}
.mk{font-size:10px;font-weight:800;border-radius:5px;padding:3px 5px;background:#fff;border:1px solid var(--border)}
.visa{color:#1A1F71;font-style:italic}
.mc{display:inline-flex;align-items:center}
.mc i{width:12px;height:12px;border-radius:50%;display:inline-block}
.secure{display:flex;gap:10px;align-items:flex-start;font-size:13px;color:var(--muted);line-height:1.45;margin:16px 0 18px}
.btn{height:56px;border-radius:28px;background:var(--black);color:#fff;font-weight:700;font-size:17px;display:flex;align-items:center;justify-content:center;margin-top:10px}
.btn.ghost{background:#fff;color:var(--ink);border:1px solid var(--borderStrong)}
.btn.lime{background:var(--lime);color:var(--ink)}
.center{text-align:center}
.icon{width:72px;height:72px;border-radius:50%;margin:28px auto 14px;display:flex;align-items:center;justify-content:center}
.status{font-weight:700;font-size:15px;margin-bottom:6px}
.big{font-size:38px;font-weight:800;letter-spacing:-.03em}
.sub{color:var(--muted);font-size:16px;margin-top:6px;line-height:1.45}
.card{background:#fff;border:1px solid var(--border);border-radius:20px;margin-top:20px;overflow:hidden}
.card .r{display:flex;justify-content:space-between;gap:16px;padding:15px 18px;border-bottom:1px solid var(--divider);font-size:15px}
.card .r:last-child{border-bottom:0}
.card .r span:first-child{color:var(--muted)}
.card .r span:last-child{font-weight:700;text-align:right}
.banner{border-radius:16px;padding:14px 16px;font-size:14px;line-height:1.45;margin-bottom:16px}
.banner b{display:block;margin-bottom:2px;font-size:15px}
.warn{background:var(--warningSurface);color:var(--warningText)}
.dang{background:var(--dangerSurface);color:var(--danger)}
.ok{background:var(--successSurface);color:var(--success)}
.steps{margin-top:18px;background:#fff;border:1px solid var(--border);border-radius:20px;padding:6px 18px}
.step{display:flex;gap:14px;padding:13px 0;border-bottom:1px solid var(--divider);font-size:15px;line-height:1.4;align-items:flex-start}
.step:last-child{border-bottom:0}
.n{flex:none;width:28px;height:28px;border-radius:50%;background:var(--sunken);font-weight:700;font-size:13px;display:flex;align-items:center;justify-content:center}
.spin{border:6px solid var(--limeSurface);border-top-color:var(--limeBorder)}
.tag{display:inline-block;font-size:12px;font-weight:700;border-radius:999px;padding:4px 10px}
.foot{position:relative}
.wide .twocol{display:grid;grid-template-columns:1fr 1fr;gap:12px}
"""

CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#141414" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'
BACK = '<div class="back"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#141414" stroke-width="2.6" stroke-linecap="round"><path d="M15 5l-7 7 7 7"/></svg></div>'
LOCK = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#5E5E5A" stroke-width="2.2" stroke-linecap="round" style="flex:none;margin-top:1px"><rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8 11V8a4 4 0 018 0v3"/></svg>'
MC = '<span class="mk mc"><i style="background:#EB001B"></i><i style="background:#F79E1B;margin-left:-5px;opacity:.9"></i></span>'
VISA = '<span class="mk visa">VISA</span>'


def hero(title="91 Day Treasury Bill", amt="UGX 1,000,000", line="Your investment starts as soon as the payment is confirmed."):
    return f'<div class="hero"><div class="p">{title}</div><div class="amt">{amt}</div><div class="p">{line}</div></div>'


def opt(badge, t, s, state="", inner="", right=None):
    if right is None:
        right = f'<div class="check">{CHECK}</div>' if state == "sel" else ('' if state == "off" else '<div class="radio"></div>')
    inner_html = f'<div class="inner">{inner}</div>' if inner else ""
    return f'<div class="opt {state}"><div class="row"><div class="badge">{badge}</div><div class="grow"><div class="t">{t}</div><div class="s">{s}</div></div>{right}</div>{inner_html}</div>'


SECURE = f'<div class="secure">{LOCK}<span>Mobile money is prompted on your phone through Yo! Payments. Cards are paid on Pesapal\'s secure page. Both are licensed by the Bank of Uganda. Finvest never sees your PIN or card number.</span></div>'
CHIPS = ('<div style="display:flex;gap:8px;margin-bottom:10px">'
         '<div style="flex:1;border:2px solid var(--ink);border-radius:12px;padding:10px;text-align:center;font-weight:700;background:#fff">MTN</div>'
         '<div style="flex:1;border:1px solid var(--border);border-radius:12px;padding:11px;text-align:center;font-weight:700;color:var(--muted);background:#fff">Airtel</div></div>')
MOMO_INNER = (CHIPS + '<div class="field"><span>Your number</span><b>+256 772 123 456</b></div>'
              '<p class="note">MTN sends a prompt to the number on your Finvest account. You approve it with your Mobile Money PIN. No charge.</p>')
CARD_INNER = ('<div class="lines"><div class="l"><span>Investment</span><span>UGX 1,000,000</span></div>'
              '<div class="l"><span>Card charge</span><span>UGX 35,000</span></div>'
              '<div class="l"><span>You pay</span><span>UGX 1,035,000</span></div></div>'
              '<p class="note">The card charge goes to the card network and Pesapal, not to Finvest. Up to UGX 5,000,000 a payment. '
              'If this money is ever refunded, it goes back to the same card.</p>')
BANK_ROW = opt("Bank", "Bank transfer", "Send from your bank, then upload the receipt", right='<span class="chev">&#8250;</span>')
MOMO_B = '<span style="font-size:10px;line-height:1.1;text-align:center">MTN<br>Airtel</span>'
CARD_B = f'{VISA}{MC}'


def pay_choose(method):
    momo = opt(MOMO_B, "Mobile money", "MTN or Airtel, prompted on your phone", "sel" if method == "momo" else "", MOMO_INNER if method == "momo" else "")
    card = opt(CARD_B, "Card", f'Visa or Mastercard, debit or credit', "sel" if method == "card" else "", CARD_INNER if method == "card" else "")
    amount = "UGX 1,000,000" if method == "momo" else "UGX 1,035,000"
    return (BACK + '<h1>Pay for your investment</h1>' + hero() + '<div class="label">Pay with</div>' + momo + card + BANK_ROW
            + SECURE + f'<div class="btn">Continue to pay {amount}</div>')


def handover():
    steps = [
        "Pesapal's payment page opens. Check that it says Finvest and UGX 1,035,000.",
        "Type your card details there. Finvest never sees them.",
        "Your bank may ask you to confirm, with a code by SMS or in its app.",
        "You come back here by yourself. Your investment starts once Pesapal confirms the payment.",
    ]
    s = "".join(f'<div class="step"><div class="n">{i+1}</div><div>{t}</div></div>' for i, t in enumerate(steps))
    return (BACK + '<h1>Finish on Pesapal\'s secure page</h1>'
            '<p class="sub" style="margin-top:-6px">Card payment, UGX 1,035,000 for your 91 Day Treasury Bill.</p>'
            f'<div class="steps">{s}</div>'
            + f'<div class="secure" style="margin:18px 0 6px">{LOCK}<span>Pesapal is licensed by the Bank of Uganda. Your card details stay with Pesapal and your bank.</span></div>'
            + '<div class="btn">Open the secure page</div><div class="btn ghost">Pay another way</div>')


PHONE = '<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#2A3800" stroke-width="2.2" stroke-linecap="round"><rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/></svg>'


def approve_phone():
    return (f'<div class="center"><div class="icon" style="background:var(--limeSurface)">{PHONE}</div>'
            '<div class="status" style="color:var(--limeBorder)">Waiting for you</div><div class="big">Approve on your phone</div>'
            '<p class="sub">MTN sent a prompt to +256 772 123 456. Enter your Mobile Money PIN to pay UGX 1,000,000 to Finvest.</p></div>'
            '<div class="card"><div class="r"><span>Paying with</span><span>MTN Mobile Money</span></div>'
            '<div class="r"><span>For</span><span>91 Day Treasury Bill</span></div>'
            '<div class="r"><span>Amount</span><span>UGX 1,000,000</span></div></div>'
            '<p class="note" style="text-align:center;margin-top:16px">No prompt after a minute? Check that your phone has signal and is not busy in another menu. '
            'We never send a second prompt for the same payment, so you cannot pay twice.</p>'
            '<div class="btn ghost">Pay another way</div><div class="btn">Back to home</div>')


def confirming():
    return ('<div class="center"><div class="icon spin"></div><div class="status" style="color:var(--limeBorder)">Waiting for Pesapal</div>'
            '<div class="big">UGX 1,035,000</div><p class="sub">Confirming your card payment. This usually takes less than a minute. '
            'You can leave this screen. We will tell you when it is done.</p></div>'
            '<div class="card"><div class="r"><span>Paying with</span><span>Card</span></div>'
            '<div class="r"><span>For</span><span>91 Day Treasury Bill</span></div>'
            '<div class="r"><span>Started</span><span>15 Nov 2026, 00:44</span></div></div>'
            '<p class="note" style="text-align:center;margin-top:16px">Closed the card page before you finished?</p>'
            '<div class="btn ghost">Open the card page again</div><div class="btn">Back to home</div>')


def paid():
    return (f'<div class="center"><div class="icon" style="background:var(--lime)">{CHECK.replace("14", "30")}</div>'
            '<div class="status" style="color:var(--success)">Completed</div><div class="big">UGX 1,000,000</div>'
            '<p class="sub">Paid into 91 Day Treasury Bill. Your investment has started.</p></div>'
            '<div class="card"><div class="r"><span>Paid by</span><span>Visa card ending 0010</span></div>'
            '<div class="r"><span>Card charge</span><span>UGX 35,000</span></div>'
            '<div class="r"><span>Total taken from your card</span><span>UGX 1,035,000</span></div>'
            '<div class="r"><span>Confirmed</span><span>15 Nov 2026, 00:46</span></div>'
            '<div class="r"><span>Reference</span><span>1001004827</span></div></div>'
            '<div class="btn ghost" style="margin-top:24px">Share receipt</div><div class="btn">Done</div>')


def failed():
    x = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#B42318" stroke-width="3" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>'
    return (f'<div class="center"><div class="icon" style="background:var(--dangerSurface)">{x}</div>'
            '<div class="status" style="color:var(--danger)">Not paid</div><div class="big">Your card was declined</div>'
            '<p class="sub">No money was taken. Your bank did not approve the payment. You can try another card or pay by mobile money. '
            'Your investment keeps its rate until 18 Nov 2026.</p></div>'
            '<div class="card"><div class="r"><span>Tried</span><span>Mastercard ending 4417</span></div>'
            '<div class="r"><span>Amount</span><span>UGX 1,035,000</span></div>'
            '<div class="r"><span>When</span><span>15 Nov 2026, 00:51</span></div></div>'
            '<div class="btn" style="margin-top:24px">Try again</div><div class="btn ghost">Pay another way</div>')


def over_limits():
    momo = opt(MOMO_B, "Mobile money", "MTN or Airtel, prompted on your phone", "sel", MOMO_INNER)
    card = opt(CARD_B, "Card", "Up to UGX 5,000,000 a payment", "off")
    return (BACK + '<h1>Pay for your investment</h1>' + hero(amt="UGX 8,000,000")
            + '<div class="banner warn"><b>Cards take up to UGX 5,000,000</b>Pay this one by mobile money or bank transfer, or invest a smaller amount by card.</div>'
            + '<div class="label">Pay with</div>' + momo + card + BANK_ROW
            + '<div class="btn" style="margin-top:20px">Continue to pay UGX 8,000,000</div>')


def paused():
    momo = opt(MOMO_B, "Mobile money", "MTN or Airtel, prompted on your phone", "sel", MOMO_INNER)
    card = opt(CARD_B, "Card", "Paused for now. Try mobile money or a bank transfer.", "off")
    return (BACK + '<h1>Pay for your investment</h1>' + hero()
            + '<div class="banner warn"><b>Card payments are paused</b>Card payments are not working right now. Mobile money and bank transfer work as usual.</div>'
            + '<div class="label">Pay with</div>' + momo + card + BANK_ROW + SECURE
            + '<div class="btn">Continue to pay UGX 1,000,000</div>')


CLIENT = {
    "01-pay-mobile-money": pay_choose("momo"),
    "02-pay-card-with-charge": pay_choose("card"),
    "03-approve-on-your-phone": approve_phone(),
    "04-finish-on-pesapal": handover(),
    "05-confirming-card": confirming(),
    "06-paid-by-card": paid(),
    "07-card-declined": failed(),
    "08-above-card-limit": over_limits(),
    "09-card-payments-paused": paused(),
}

# ---------------- console ----------------
CON_CSS = TOKENS + """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,sans-serif;background:var(--paper);color:var(--ink);font-size:14px}
.shell{display:flex;min-height:100vh}
.side{width:220px;background:var(--black);color:#fff;padding:18px 16px;flex:none}
.logo{display:flex;align-items:center;gap:8px;font-weight:700;font-size:17px;margin-bottom:18px}
.logo i{width:24px;height:24px;border-radius:6px;background:#222;display:inline-block;position:relative}
.logo i:after{content:"";position:absolute;right:4px;top:5px;width:5px;height:5px;border-radius:50%;background:var(--lime)}
.grp{font-size:10px;letter-spacing:.1em;color:#C9C9C4;font-weight:700;margin:14px 6px 6px}
.nav{padding:7px 10px;border-radius:8px;font-size:13px;font-weight:600;margin-bottom:2px}
.nav.on{background:var(--lime);color:var(--ink)}
.main{flex:1;padding:28px 32px;max-width:1220px}
h1{font-size:24px;font-weight:700;letter-spacing:-.02em}
.lead{color:var(--muted);margin:4px 0 20px}
h2{font-size:17px;font-weight:700;margin:22px 0 4px}
.panel{background:#fff;border:1px solid var(--border);border-radius:14px;padding:18px;margin-top:12px}
.row{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--divider)}
.row:last-child{border-bottom:0}
.grow{flex:1}
.muted{color:var(--muted)}
.small{font-size:12px}
.pill{display:inline-block;font-size:12px;font-weight:700;border-radius:999px;padding:4px 10px;white-space:nowrap}
.p-ok{background:var(--successSurface);color:var(--success)}
.p-warn{background:var(--warningSurface);color:var(--warningText)}
.p-dang{background:var(--dangerSurface);color:var(--danger)}
.p-n{background:var(--sunken);color:var(--muted)}
.b{border-radius:999px;border:1px solid var(--borderStrong);background:#fff;padding:8px 14px;font-weight:700;font-size:13px;white-space:nowrap}
.b.pri{background:var(--black);color:#fff;border-color:var(--black)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.kv{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--divider)}
.kv:last-child{border-bottom:0}
.kv span:last-child{font-weight:600;text-align:right}
.banner{border-radius:12px;padding:12px 14px;margin-top:12px;line-height:1.45}
.warn{background:var(--warningSurface);color:var(--warningText);border:1px solid #F0C48A}
.inp{border:1px solid var(--borderStrong);border-radius:10px;padding:10px 12px;background:#fff;margin-top:6px}
.lbl{font-weight:700;font-size:12px;margin-top:12px}
.tl{border-left:2px solid var(--border);margin-left:6px;padding-left:16px}
.tl div{position:relative;padding:6px 0}
.tl div:before{content:"";position:absolute;left:-22px;top:11px;width:10px;height:10px;border-radius:50%;background:var(--borderStrong)}
.tl div.on:before{background:var(--lime);border:1px solid var(--limeBorder)}
.top{display:none}
.phone .side{display:none}
.phone .top{display:flex;justify-content:space-between;align-items:center;background:var(--black);color:#fff;padding:12px 16px}
.phone .main{padding:18px 16px}
.phone .grid2,.phone .grid3{grid-template-columns:1fr}
.phone .row{flex-wrap:wrap}
"""

NAV = [("DAILY WORK", ["Overview", "Review Queue", "Maturities"]),
       ("CLIENTS AND MONEY", ["Clients", "Money Movement", "Reconciliation", "Investments", "Automatic Investing", "Goals", "Dealing Desk", "Finance"]),
       ("CATALOGUE", ["Products", "Rates"]), ("CONTROL", ["Reports and Audit", "Settings"])]


def shell(active, body):
    nav = ""
    for g, items in NAV:
        nav += f'<div class="grp">{g}</div>' + "".join(f'<div class="nav {"on" if i == active else ""}">{i}</div>' for i in items)
    return (f'<div class="top"><div class="logo" style="margin:0"><i></i>finvest ops</div><span class="b" style="background:transparent;color:#fff;border-color:#4D4D4D">Menu</span></div>'
            f'<div class="shell"><div class="side"><div class="logo"><i></i>finvest ops</div>{nav}</div><div class="main">{body}</div></div>')


def con_settings():
    ch = [("MTN pay in", "Yo! Payments", "Open", "p-ok"), ("Airtel pay in", "Yo! Payments", "Open", "p-ok"), ("Card pay in", "Pesapal", "Pause waiting for a second person", "p-warn"),
          ("Bank transfer pay in", "Trust account", "Open", "p-ok"), ("MTN and Airtel payouts", "Yo! Payments", "Open", "p-ok"),
          ("Bank payouts", "Trust account, two-person batches", "Open", "p-ok")]
    rows = "".join(f'<div class="row"><div class="grow"><b>{a}</b><div class="muted small">{p}</div></div><span class="pill {c}">{s}</span>'
                   f'<span class="b">{"Approve or reject" if c == "p-warn" else "Propose a pause"}</span></div>' for a, p, s, c in ch)
    body = ('<h1>Settings</h1><p class="lead">The rules the platform runs on. Every change takes two people.</p>'
            '<h2>Payment channels</h2><p class="muted">A paused channel refuses new payments. A payment already started finishes as it would have.</p>'
            f'<div class="panel">{rows}</div>'
            '<div class="banner warn"><b>Grace A. proposed pausing card pay in, 15 Nov, 00:20.</b> Reason: Pesapal reports card declines across all banks. '
            'You can approve or reject it. Grace can only withdraw it.</div>'
            '<h2>Card limits</h2><p class="muted">Checked before a card payment is saved, to make stolen cards hard to cash out.</p>'
            '<div class="panel grid3"><div><div class="muted small">Card, one payment</div><div style="font-size:20px;font-weight:700;margin-top:4px">UGX 5,000,000</div></div>'
            '<div><div class="muted small">Card, per client in 30 days</div><div style="font-size:20px;font-weight:700;margin-top:4px">UGX 10,000,000</div></div>'
            '<div><div class="muted small">Card money refunded</div><div style="font-size:20px;font-weight:700;margin-top:4px">To the same card</div></div></div>'
            '<div style="margin-top:12px"><span class="b">Propose new limits</span></div>')
    return shell("Settings", body)


def con_payment():
    body = ('<div class="muted small">Clients &rsaquo; Amara Nakato &rsaquo; Payments</div>'
            '<h1 style="margin-top:6px">Card payment, UGX 1,035,000</h1><p class="lead">For 91 Day Treasury Bill, investment started 15 Nov 2026.</p>'
            '<div class="grid2"><div class="panel"><b>Payment</b>'
            '<div class="kv"><span class="muted">Status</span><span><span class="pill p-ok">Confirmed</span></span></div>'
            '<div class="kv"><span class="muted">Paid by</span><span>Visa card ending 0010</span></div>'
            '<div class="kv"><span class="muted">Investment</span><span>UGX 1,000,000</span></div>'
            '<div class="kv"><span class="muted">Card charge, paid by the client</span><span>UGX 35,000</span></div>'
            '<div class="kv"><span class="muted">Pesapal tracking ID</span><span>b945e4af-80a5-4ec1-8706-e03f8332fb04</span></div>'
            '<div class="kv"><span class="muted">Pesapal confirmation code</span><span>AA11BB22</span></div>'
            '<div class="kv"><span class="muted">Our reference</span><span>dep_01JD8Q4X7K2M</span></div>'
            '<div class="kv"><span class="muted">Same card on another client</span><span>No</span></div></div>'
            '<div class="panel"><b>What happened</b><div class="tl" style="margin-top:10px">'
            '<div class="on">Asked to pay by card <div class="muted small">15 Nov, 00:44, by the client</div></div>'
            '<div class="on">Pesapal order opened <div class="muted small">00:44, worker</div></div>'
            '<div class="on">Pesapal notice received, status read <div class="muted small">00:46</div></div>'
            '<div class="on">Confirmed for exactly UGX 1,035,000 <div class="muted small">00:46, from Pesapal\'s status read</div></div>'
            '<div class="on">Investment started <div class="muted small">00:46</div></div></div>'
            '<div style="display:flex;gap:8px;margin-top:16px;flex-wrap:wrap"><span class="b">Propose a refund to the card</span><span class="b">Take the payment back</span></div>'
            '<p class="muted small" style="margin-top:8px">A refund goes back to Visa ending 0010 only. A second person approves it first.</p></div></div>')
    return shell("Clients", body)


def con_refunds():
    items = [("UGX 1,035,000", "Card refund, investment cancelled", "To Visa ending 0010", "Needs a second person", "p-warn", "Proposed by Grace A., 15 Nov, 09:12"),
             ("UGX 600,000", "Card refund, paid twice by mistake", "To Visa ending 8812", "Approve it on Pesapal too", "p-warn", "Approved by Fred F., 15 Nov, 10:02"),
             ("UGX 450,000", "Card refund, early withdrawal within 30 days", "To Mastercard ending 4417", "Sent, waiting for the statement", "p-n", "Pesapal approved 14 Nov, 16:40"),
             ("UGX 2,000,000", "Card refund", "To Visa ending 2231", "Done", "p-ok", "On the Pesapal statement 13 Nov")]
    rows = "".join(f'<div class="row"><div style="width:150px"><b>{a}</b></div><div class="grow"><b>{t}</b><div class="muted small">{d} · {w}</div></div>'
                   f'<span class="pill {c}">{s}</span></div>' for a, t, d, s, c, w in items)
    body = ('<h1>Reconciliation</h1><p class="lead">The ledger compared with provider and bank statements, and anything that does not match.</p>'
            '<h2>Card refunds</h2><p class="muted">Card money goes back only to the card it came from. One person proposes, a different person approves, '
            'then someone approves it on Pesapal\'s dashboard. Pesapal takes one refund per payment.</p>'
            f'<div class="panel">{rows}</div>'
            '<div class="grid2" style="margin-top:4px"><div class="panel"><b>Approve: card refund, investment cancelled</b>'
            '<div class="kv"><span class="muted">Client</span><span>Amara Nakato, 1001004827</span></div>'
            '<div class="kv"><span class="muted">Paid by</span><span>Visa card ending 0010</span></div>'
            '<div class="kv"><span class="muted">Refund, in full</span><span>UGX 1,035,000</span></div>'
            '<div class="kv"><span class="muted">Investment</span><span>Cancelled, never started</span></div>'
            '<div class="kv"><span class="muted">Proposed by</span><span>Grace A.: the client changed their mind</span></div>'
            '<div style="display:flex;gap:8px;margin-top:14px"><span class="b pri">Approve with passkey</span><span class="b">Send back</span></div></div>'
            '<div class="panel"><b>After approving here</b><p class="muted" style="margin-top:8px;line-height:1.5">Finvest sends the refund to Pesapal. Pesapal then asks for it to be approved on its own dashboard. '
            'Once someone has done that, record it here. The refund is done when it shows on the next Pesapal statement, which the day close checks.</p>'
            '<div style="margin-top:12px"><span class="b">Record Pesapal\'s approval</span></div></div></div>')
    return shell("Reconciliation", body)


def con_close():
    def pot(name, st, led, diff, pill, pc):
        return (f'<div class="panel"><div style="display:flex;justify-content:space-between"><b>{name}</b><span class="pill {pc}">{pill}</span></div>'
                f'<div class="kv"><span class="muted">Statement closing</span><span>{st}</span></div><div class="kv"><span class="muted">Ledger closing</span><span>{led}</span></div>'
                f'<div class="kv"><span class="muted">Open differences</span><span>{diff}</span></div><div class="kv"><span class="muted">Unexplained</span><span>UGX 0</span></div></div>')
    body = ('<h1>Reconciliation</h1><p class="lead">The ledger compared with provider and bank statements, and anything that does not match.</p>'
            '<h2>Close the day, Sat 14 Nov 2026</h2><p class="muted">One person prepares the close and another approves it.</p>'
            '<div class="grid3">' + pot("Pesapal balance", "UGX 18,240,000", "UGX 18,240,000", "None", "Ready", "p-ok")
            + pot("Trust bank account, Stanbic trust", "UGX 412,500,000", "UGX 424,500,000", "Settlement on its way, UGX 12,000,000", "Explained", "p-ok")
            + pot("Yo! Payments account", "UGX 9,450,000", "UGX 9,450,000", "None", "Ready", "p-ok") + '</div>'
            '<div style="margin-top:12px"><span class="b pri">Prepare the close</span></div>'
            '<h2>Pesapal settlements</h2><p class="muted">Pesapal pays card collections to the trust account about two working days after a withdrawal is asked for on its dashboard. '
            'Record each one here. A different person approves it, which posts it to the books.</p>'
            '<div class="panel grid3"><div><div class="lbl" style="margin-top:0">Pesapal sent (UGX)</div><div class="inp">12,000,000</div></div>'
            '<div><div class="lbl" style="margin-top:0">Arrived at the trust account (UGX)</div><div class="inp">12,000,000</div></div>'
            '<div><div class="lbl" style="margin-top:0">Pesapal withdrawal reference</div><div class="inp">WD-55123</div></div></div>'
            '<div style="margin-top:12px"><span class="b pri">Record settlement</span> <span class="muted small" style="margin-left:8px">Nothing is posted on one person\'s word.</span></div>')
    return shell("Reconciliation", body)


CONSOLE = {
    "c1-settings-payment-channels": con_settings(),
    "c2-card-payment-detail": con_payment(),
    "c3-card-refunds": con_refunds(),
    "c4-close-the-day": con_close(),
}

for name, body in CLIENT.items():
    for lay in ("phone", "wide"):
        (OUT / f"client-{lay}-{name}.html").write_text(
            f'<!doctype html><meta charset=utf-8><style>{CLIENT_CSS}</style><body class="{lay}"><div class="col">{body}</div>')
for name, body in CONSOLE.items():
    for lay in ("phone", "wide"):
        (OUT / f"console-{lay}-{name}.html").write_text(
            f'<!doctype html><meta charset=utf-8><style>{CON_CSS}</style><body class="{lay}">{body}')
print(len(list(OUT.iterdir())))
