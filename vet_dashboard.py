"""crittr.ai — the vet's division: one screen for what their book of business earns.

WHY THIS FILE EXISTS. By the time this was written, crittr already had two separate,
working revenue streams for a partner veterinarian: video consults (consults.py, paid
through Stripe Connect destination charges) and product sales attributed to them — either
because they wrote the product into an aftercare plan (vet_aftercare.py) or because the
buyer is a claimed client of their practice (vet_practice.py). Both streams already worked.
Neither had anywhere a vet could see them side by side. A vet who wanted their number had to
know that /vet/practice showed product revenue, that consult revenue was a JSON endpoint
nobody rendered, and that /vet/payouts — the URL Stripe onboarding already redirects back
to — didn't exist as a page at all.

This file adds no revenue logic and no tables. Every number on every screen here comes from
a function vet_practice.py, consults.py or connect_payouts.py already exposes; this is
purely the surface that reads them together. That is deliberate — the money-moving code is
exactly the code you do not want two people editing for two different reasons in the same
sitting.

THREE SCREENS:

  /vet/dashboard   the division, at a glance: this month's revenue by stream, the case
                    queue, the client book funnel, whether payouts are set up.
  /vet/consults     set your video-consult fee, see who paid, see what you kept.
  /vet/payouts      connect a bank account (Stripe Express) and see what's been paid and
                    what's still on the open statement.

/vet/payouts in particular was a dangling reference before this file: connect_payouts.py's
onboarding_link() already sends Stripe's redirect there
(refresh_url=f"{app_url}/vet/payouts?...", return_url=...) — a vet who finished onboarding
landed on a 404. This closes that loop with no change to connect_payouts.py itself.
"""
from flask import session

_CSS = """
<style>
  :root{--ink:#1C2A1F;--muted:#6E7D70;--line:#DFE5DB;--sage:#527E54;--sage-d:#3E6340;
        --cream:#FDFBF5;--warn:#B4541F;--er:#A32020}
  *{box-sizing:border-box}
  body{margin:0;background:var(--cream);color:var(--ink);
       font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .wrap{max-width:880px;margin:0 auto;padding:32px 20px 64px}
  .brand{font-weight:800;letter-spacing:-.02em;font-size:22px;color:var(--sage-d);
         text-decoration:none;display:inline-block}
  nav.vetnav{margin:6px 0 26px} nav.vetnav a{color:var(--sage-d);margin-right:16px;
         font-size:14px;font-weight:600;text-decoration:none}
  nav.vetnav a.here{border-bottom:2px solid var(--sage)}
  h1{font-size:30px;line-height:1.2;margin:0 0 10px;letter-spacing:-.02em}
  h2{font-size:18px;margin:0 0 12px}
  .sub{color:var(--muted);margin:0 0 28px}
  .card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px;
        margin-bottom:18px}
  label{display:block;font-weight:600;font-size:14px;margin:14px 0 6px}
  input,select,textarea{width:100%;padding:11px 12px;border:1px solid var(--line);
        border-radius:9px;font:inherit;background:#fff}
  .row{display:flex;gap:12px}.row>div{flex:1}
  button{background:var(--sage);color:#fff;border:0;border-radius:9px;padding:13px 20px;
         font:inherit;font-weight:700;cursor:pointer;margin-top:20px}
  button:hover{background:var(--sage-d)}
  button:disabled{background:#B9C7B8;cursor:not-allowed}
  button.ghost{background:#fff;color:var(--sage-d);border:1px solid var(--line)}
  .note{background:#F2F7F1;border:1px solid #C7DEC4;border-radius:10px;padding:14px;
        font-size:14px;color:var(--sage-d);margin-top:18px}
  .warn{background:#FFF6E5;border:1px solid #E8D9A8;color:#8A5A10}
  .msg{padding:13px;border-radius:9px;margin-top:16px;display:none;font-size:15px}
  .ok{background:#EAF5E9;border:1px solid #A6C9A2;color:#2D4A30}
  .bad{background:#FBEDEA;border:1px solid #E7C3B9;color:#8A2C10}
  .meta{color:var(--muted);font-size:13px;margin:8px 0}
  .empty{text-align:center;color:var(--muted);padding:34px 12px}
  .stats{display:flex;gap:12px;flex-wrap:wrap;margin:4px 0 0}
  .stat{flex:1;min-width:150px;border:1px solid var(--line);border-radius:12px;
        padding:14px;background:#fff}
  .stat b{display:block;font-size:26px;letter-spacing:-.02em}
  .stat span{color:var(--muted);font-size:13px}
  .stat.total{background:#F2F7F1;border-color:#C7DEC4}
  .stat.total b{color:var(--sage-d)}
  .pill{display:inline-block;font-size:11px;font-weight:800;letter-spacing:.05em;
        padding:4px 9px;border-radius:99px;color:#fff;text-transform:uppercase}
  .p-on{background:var(--sage)} .p-off{background:var(--muted)} .p-warn{background:var(--warn)}
  table{width:100%;border-collapse:collapse;font-size:14px;margin-top:14px}
  th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;
     text-transform:uppercase;letter-spacing:.04em;padding:6px 8px}
  td{padding:8px;border-top:1px solid var(--line)}
  .tag{font-size:11px;font-weight:800;padding:3px 8px;border-radius:99px;
       background:#EEF2ED;color:var(--sage-d)}
  code{background:#F2F1EC;padding:2px 6px;border-radius:5px;font-size:13px}
</style>
"""

# One place that knows the vet-facing portal's shape, so every screen links to every other
# screen the same way instead of each page inventing its own subset of the map.
_NAV = [
    ("/vet/dashboard", "Dashboard"),
    ("/vet", "Case queue"),
    ("/vet/practice", "Practice & clients"),
    ("/vet/consults", "Video consults"),
    ("/vet/payouts", "Payouts"),
    ("/vet/patients", "Patient records"),
]


def _nav_html(current):
    links = "".join(
        f'<a href="{href}"{" class=here" if href == current else ""}>{label}</a>'
        for href, label in _NAV
    )
    return f'<nav class=vetnav>{links}</nav>'


def _page(title, body, current=None):
    nav = _nav_html(current) if current else ""
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title} · crittr for veterinarians</title>{_CSS}</head><body>"
            f"<div class=wrap><a class=brand href='/vet/dashboard'>crittr</a>{nav}{body}"
            f"</div></body></html>")


DASHBOARD_HTML = _page("Your division", """
<h1>Your division</h1>
<p class=sub id=sub>Loading…</p>

<div id=gate style=display:none class=card>
  <strong>You are not signed in as a partner veterinarian.</strong>
  <p class=meta>This page is for verified partner vets.</p>
  <a href="/vet/apply"><button>Apply to partner</button></a>
</div>

<div id=body style=display:none>

  <div class=card>
    <h2>Last 30 days, across everything</h2>
    <div class=stats id=revenue></div>
    <p class=meta id=revnote></p>
  </div>

  <div class=card>
    <h2>Video consults</h2>
    <p class=meta id=consultsub>Loading…</p>
    <a href="/vet/consults"><button class=ghost>Set your fee &amp; view consults</button></a>
  </div>

  <div class=card>
    <h2>Case queue</h2>
    <p class=meta id=casesub>Loading…</p>
    <a href="/vet"><button class=ghost>Open the case queue</button></a>
  </div>

  <div class=card>
    <h2>Your client book</h2>
    <p class=meta id=booksub>Loading…</p>
    <a href="/vet/practice"><button class=ghost>Manage your practice &amp; clients</button></a>
  </div>

  <div class=card id=payoutcard>
    <h2>Getting paid</h2>
    <p class=meta id=payoutsub>Loading…</p>
    <a href="/vet/payouts"><button class=ghost>Open payouts</button></a>
  </div>

</div>

<script>
function money(c){ return '$' + ((c||0)/100).toFixed(2); }
function el(id){ return document.getElementById(id); }
function stat(v,l){ return '<div class=stat><b>'+v+'</b><span>'+l+'</span></div>'; }
function statTotal(v,l){ return '<div class="stat total"><b>'+v+'</b><span>'+l+'</span></div>'; }

async function j(url){
  const r = await fetch(url);
  return {ok:r.ok, status:r.status, body: await r.json().catch(()=>({}))};
}

async function load(){
  const me = await j('/api/vet/me');
  if(!me.ok){
    el('sub').textContent='';
    el('gate').style.display='block';
    return;
  }
  el('sub').innerHTML = (me.body.active_states||[]).length
    ? 'Licensed and active in <strong>'+me.body.active_states.join(', ')+'</strong>.'
    : 'Your account is not yet cleared to see cases — everything else here still works.';
  el('body').style.display='block';

  const [cases, practice, consults, payouts] = await Promise.all([
    j('/api/vet/cases'), j('/api/vet/practice'), j('/api/vet/consults'),
    j('/api/vet/practice/payouts')
  ]);

  // ── combined revenue: three streams, one total. Every cent here is what the vet or
  // their practice actually keeps (share_cents / vet_amount_cents) — never the gross
  // amount a customer paid — so this total is directly comparable to what lands in
  // their bank account.
  const bySource = (practice.body.earnings||{}).by_source || {};
  const planCents = (bySource.plan||{}).cents || 0;
  const practiceCents = (bySource.practice||{}).cents || 0;
  const consultCents = (consults.body.earnings||{}).earned_cents || 0;
  const totalCents = planCents + practiceCents + consultCents;
  el('revenue').innerHTML =
    stat(money(consultCents), 'video consults') +
    stat(money(planCents), 'products you recommended') +
    stat(money(practiceCents), 'products your clients bought') +
    statTotal(money(totalCents), 'total, last 30 days');
  el('revnote').textContent = practice.body.practice
    ? 'Product revenue share: ' + (practice.body.practice.rev_share_pct||10) +
      '% of what the customer actually paid, frozen per order at sale time.'
    : 'Set up your practice to start earning on the clients you already see.';

  // ── video consults ──────────────────────────────────────────────────────────────
  const ce = consults.body.earnings || {};
  el('consultsub').textContent = ce.consults
    ? ce.consults + ' paid consult' + (ce.consults===1?'':'s') + ' in the last 30 days, ' +
      'you kept ' + money(ce.earned_cents) + '.'
    : 'No paid consults yet in the last 30 days.';

  // ── case queue ───────────────────────────────────────────────────────────────────
  if(cases.ok){
    const n = cases.body.count||0;
    el('casesub').textContent = n
      ? n + ' case' + (n===1?'':'s') + ' waiting for you right now.'
      : (cases.body.note || 'No cases waiting right now.');
  } else {
    el('casesub').textContent = 'Not cleared to see cases yet.';
  }

  // ── client book ──────────────────────────────────────────────────────────────────
  const book = practice.body.book;
  if(book){
    const c = book.counts||{};
    el('booksub').textContent = book.total
      ? book.total + ' clients in your book · ' + (c.claimed||0) + ' connected' +
        (book.claim_rate_pct!=null ? ' (' + book.claim_rate_pct + '% of those invited)' : '')
      : 'Your book is empty — bring your client list across to start earning on it.';
  } else {
    el('booksub').textContent = 'Set up your practice to bring your client book across.';
  }

  // ── payouts ──────────────────────────────────────────────────────────────────────
  if(payouts.ok){
    const st = payouts.body.stripe || {};
    const open = payouts.body.open_statement || {};
    el('payoutsub').innerHTML = st.payouts_enabled
      ? 'Bank account connected. ' + money(open.owed_cents) + ' on your open statement.'
      : '<span class="pill p-warn">Not connected</span> Connect a bank account to get paid ' +
        'what you\\'ve earned.';
  } else {
    el('payoutsub').textContent = 'Set up your practice first to connect a bank account.';
  }
}
load();
</script>
""", current="/vet/dashboard")


CONSULTS_HTML = _page("Video consults", """
<h1>Video consults</h1>
<p class=sub>Set what you charge for a video consult. crittr takes a platform fee off the
top and the rest settles straight to your practice's connected bank account — same place
your product revenue share lands.</p>

<div id=gate style=display:none class=card>
  <strong>You are not signed in as a partner veterinarian.</strong>
  <a href="/vet/apply"><button>Apply to partner</button></a>
</div>

<div id=body style=display:none>

  <div class=card>
    <h2>Your fee</h2>
    <form id=ff>
      <label>Consult fee (USD)</label>
      <input name=amount id=amount type=number step="0.01" min="15" max="500" required>
      <label>What a client sees under the price (optional)</label>
      <input name=blurb id=blurb placeholder="15-minute video visit">
      <button type=submit>Save fee</button>
    </form>
    <div id=fmsg class=msg></div>
    <p class=meta id=split></p>
  </div>

  <div class=card>
    <h2>Last 30 days</h2>
    <div class=stats id=stats></div>
  </div>

  <div class=card>
    <h2>Recent consults</h2>
    <div id=list></div>
  </div>

</div>

<script>
function money(c){ return '$' + ((c||0)/100).toFixed(2); }
function el(id){ return document.getElementById(id); }
function stat(v,l){ return '<div class=stat><b>'+v+'</b><span>'+l+'</span></div>'; }

async function loadFee(){
  const r = await fetch('/api/vet/consult-fee');
  if(!r.ok){ el('gate').style.display='block'; return null; }
  const j = await r.json();
  el('body').style.display='block';
  el('amount').value = (j.amount_cents/100).toFixed(2);
  el('blurb').value = '';
  el('split').textContent = 'On a ' + money(j.amount_cents) + ' consult you keep ' +
    money(j.you_receive_cents) + ' (crittr\\'s platform fee is ' + j.crittr_fee_pct + '%). ' +
    (j.can_charge ? '' : 'You can\\'t take payment yet — connect a bank account on the ' +
     'payouts page first.');
  return j;
}

async function loadConsults(){
  const r = await fetch('/api/vet/consults');
  if(!r.ok) return;
  const j = await r.json();
  const e = j.earnings || {};
  el('stats').innerHTML =
    stat(e.consults||0, 'paid consults') +
    stat(money(e.earned_cents), 'you kept');
  const rows = j.consults || [];
  if(!rows.length){
    el('list').innerHTML = '<div class=empty>No consults yet. Once you quote one from a '+
      'case, the pay link goes straight to the owner.</div>';
    return;
  }
  el('list').innerHTML = '<table><tr><th>When</th><th>Amount</th><th>You keep</th>'+
    '<th>Status</th></tr>' + rows.slice(0,50).map(function(c){
      return '<tr><td class=meta>'+(c.created_at||'').slice(0,16).replace('T',' ')+'</td>'+
        '<td>'+money(c.amount_cents)+'</td><td>'+money(c.vet_amount_cents)+'</td>'+
        '<td><span class=tag>'+c.status+'</span></td></tr>';
    }).join('') + '</table>';
}

document.getElementById('ff').addEventListener('submit', async function(e){
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target).entries());
  const m = el('fmsg'); m.style.display='block'; m.className='msg'; m.textContent='Saving…';
  const r = await fetch('/api/vet/consult-fee', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({amount_cents: Math.round(parseFloat(d.amount)*100),
                          blurb: d.blurb})});
  const j = await r.json();
  m.className = 'msg ' + (r.ok ? 'ok' : 'bad');
  m.textContent = r.ok ? 'Fee saved.' : (j.error || 'Could not save');
  if(r.ok) loadFee();
});

loadFee().then(function(ok){ if(ok) loadConsults(); });
</script>
""", current="/vet/consults")


PAYOUTS_HTML = _page("Payouts", """
<h1>Getting paid</h1>
<p class=sub>One bank account for your practice, used for both your video consults and your
product revenue share. Stripe holds your bank details and tax identity directly — crittr
never sees them, and a compromised crittr login can never redirect your payouts.</p>

<div id=gate style=display:none class=card>
  <strong>Set up your practice first</strong>
  <p class=meta>Payouts are tied to your practice record, not to you individually.</p>
  <a href="/vet/practice"><button>Set up your practice</button></a>
</div>

<div id=body style=display:none>

  <div class=card id=connectcard>
    <h2>Bank account</h2>
    <p class=meta id=connectsub>Loading…</p>
    <button id=connect class=ghost>Connect a bank account</button>
    <div id=cmsg class=msg></div>
  </div>

  <div class=card>
    <h2>Right now</h2>
    <div class=stats id=stats></div>
    <p class=meta id=holdnote></p>
  </div>

  <div class=card>
    <h2>Payout history</h2>
    <div id=list></div>
  </div>

</div>

<script>
function money(c){ return '$' + ((c||0)/100).toFixed(2); }
function el(id){ return document.getElementById(id); }
function stat(v,l){ return '<div class=stat><b>'+v+'</b><span>'+l+'</span></div>'; }

async function load(){
  const r = await fetch('/api/vet/practice/payouts');
  if(r.status===400){ el('gate').style.display='block'; return; }
  if(!r.ok){ el('gate').style.display='block'; return; }
  const j = await r.json();
  el('body').style.display='block';

  const st = j.stripe || {};
  el('connectsub').innerHTML = st.payouts_enabled
    ? '<span class="pill p-on">Connected</span> Payouts go straight to your practice\\'s '+
      'bank account.'
    : (st.connected
        ? '<span class="pill p-warn">Pending</span> ' + (st.next||'Stripe still needs a '+
          'few details.')
        : '<span class="pill p-off">Not connected</span> ' + (st.next||''));
  el('connect').textContent = st.payouts_enabled ? 'Update bank details'
    : 'Connect a bank account';

  el('stats').innerHTML =
    stat(money(j.open_statement.owed_cents), 'earned, not yet paid') +
    stat(money(j.held_back_cents), 'still in the refund window') +
    stat(money(j.minimum_payout_cents), 'minimum payout');
  el('holdnote').textContent = 'Sales sit for ' + j.holdback_days + ' days before they\\'re '+
    'eligible to be paid out — most refunds arrive well inside that window, so a refund on '+
    'a recent sale is just arithmetic, never money you have to send back.';

  const rows = j.payouts || [];
  el('list').innerHTML = rows.length
    ? '<table><tr><th>Period</th><th>Amount</th><th>Status</th><th>Paid</th></tr>' +
      rows.map(function(p){
        return '<tr><td class=meta>'+(p.period_start||'—')+' – '+(p.period_end||'—')+'</td>'+
          '<td>'+money(p.amount_cents)+'</td>'+
          '<td><span class=tag>'+p.status+'</span></td>'+
          '<td class=meta>'+(p.paid_at ? p.paid_at.slice(0,10) : '—')+'</td></tr>';
      }).join('') + '</table>'
    : '<div class=empty>No payouts yet.</div>';
}

el('connect').addEventListener('click', async function(){
  const m = el('cmsg'); m.style.display='block'; m.className='msg'; m.textContent='Opening Stripe…';
  const r = await fetch('/api/vet/practice/connect', {method:'POST'});
  const j = await r.json();
  if(r.ok && j.onboarding_url){
    location.href = j.onboarding_url;
  } else {
    m.className='msg bad'; m.textContent = j.error || 'Could not start onboarding';
  }
});

load();
</script>
""", current="/vet/payouts")


def register_dashboard_pages(app):
    """Wire the three new screens. Purely additive: no new tables, no new API routes —
    every fetch() above calls an endpoint vet_portal.py, vet_practice.py, consults.py or
    connect_payouts.py already serves."""

    @app.route("/vet/dashboard", methods=["GET"])
    def vet_dashboard_page():
        return DASHBOARD_HTML

    @app.route("/vet/consults", methods=["GET"])
    def vet_consults_page():
        return CONSULTS_HTML

    @app.route("/vet/payouts", methods=["GET"])
    def vet_payouts_page():
        return PAYOUTS_HTML
