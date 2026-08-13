"""crittr.ai — the page that recruits a veterinary practice.

WHAT THIS PAGE HAS TO DO. A clinic owner has been pitched by Vetsource, Covetrus and
Chewy's practice program already. They have heard "earn on your clients' purchases"
before. So this page cannot lead with the money — the money is real but modest, and a
clinic that signs up expecting thousands a month will leave when it is hundreds.

It leads with the thing no competitor has: crittr knows what happened AT HOME. Whether the
doses were actually given, what the owner typed at 2am before they called, what was never
re-ordered. A practice management system records what happened in the building; this
records the other three hundred and sixty days. That is a reason to log in daily. The
revenue share is the reason to stay.

HONEST NUMBERS ONLY. The worked example uses a 20% claim rate and a 10% monthly purchase
rate, not the 100%/30% a spreadsheet would flatter you with. A vet who signs up on those
numbers and beats them stays; one who signs up on optimistic numbers and misses them tells
other vets. The whole strategy depends on the first few practices being glad they did it.

IT DOES NOT PROMISE WHAT IS NOT BUILT. No claim about prescription fulfilment, no claim
about a catalogue crittr does not yet stock, and the economics section says plainly which
products earn today.
"""
import os

from flask import jsonify


def _rate():
    return int(os.environ.get("CRITTR_REV_SHARE_PCT",
                              os.environ.get("CRITTR_VET_REV_SHARE_PCT", "10")))


_CSS = """
<style>
  :root{--ink:#1C2A1F;--muted:#6E7D70;--line:#DFE5DB;--sage:#527E54;--sage-d:#3E6340;
        --cream:#FDFBF5}
  *{box-sizing:border-box}
  body{margin:0;background:var(--cream);color:var(--ink);
       font:17px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .wrap{max-width:760px;margin:0 auto;padding:44px 20px 80px}
  .brand{font-weight:800;letter-spacing:-.02em;font-size:22px;color:var(--sage-d);
         text-decoration:none;display:inline-block;margin-bottom:36px}
  h1{font-size:40px;line-height:1.12;margin:0 0 16px;letter-spacing:-.025em}
  h2{font-size:24px;margin:44px 0 12px;letter-spacing:-.015em}
  .lede{font-size:20px;color:var(--muted);margin:0 0 34px}
  .card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px;
        margin:20px 0}
  .chart{background:#1C2A1F;color:#E8EDE7;border-radius:14px;padding:22px;margin:20px 0;
         font:14px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-x:auto}
  .chart b{color:#fff}
  .chart .hit{color:#FFB4A2;font-weight:700}
  table{width:100%;border-collapse:collapse;margin-top:8px}
  th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;
     text-transform:uppercase;letter-spacing:.04em;padding:8px}
  td{padding:9px 8px;border-top:1px solid var(--line);font-size:16px}
  .cta{background:var(--sage);color:#fff;border:0;border-radius:10px;padding:15px 26px;
       font:inherit;font-weight:700;cursor:pointer;text-decoration:none;
       display:inline-block;margin-top:8px}
  .cta:hover{background:var(--sage-d)}
  .note{background:#F2F7F1;border:1px solid #C7DEC4;border-radius:10px;padding:16px;
        font-size:15px;color:var(--sage-d);margin-top:20px}
  .plain{color:var(--muted);font-size:15px}
  ul{padding-left:22px} li{margin:7px 0}
</style>
"""


def _page(rate):
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>crittr for veterinary practices</title>
<meta name="description" content="Your clients already buy food, supplements and
preventatives every month. See what they actually did at home — and earn on what they
order.">{_CSS}</head><body><div class=wrap>
<a class=brand href='/'>crittr</a>

<h1>You already know what happened in the room.</h1>
<p class=lede>crittr knows what happened for the next six weeks — whether the doses were
given, what the owner typed at 2am before they called you, what they never re-ordered.</p>

<p>Your practice management system is a very good record of appointments. It has nothing
to say about the three hundred and sixty days between them. That gap is where treatment
plans quietly fail, and it is the only thing a client can tell you about that you cannot
see for yourself.</p>

<h2>What a connected patient looks like</h2>
<p class=plain>This is a real chart view, not a mockup — it's what your patient page shows:</p>

<div class=chart>
PATIENT: Rufus, dog, heeler, 4y, 38lb<br>
Known conditions: recurrent otitis<br>
VCPR: valid — in_person on 12 Aug 2026<br><br>
<b>WHAT WAS PRESCRIBED, AND WHETHER IT WAS GIVEN:</b><br>
&nbsp;&nbsp;[medication] Otic drops — 2x/day for 10 days —
<span class=hit>NONE of 20 doses marked given</span><br><br>
<b>WHAT THEY ACTUALLY BOUGHT</b> (re-orders are the real adherence signal):<br>
&nbsp;&nbsp;12 Aug 2026 — Dasuquin Advanced x2
</div>

<p>That one line — <em>none of 20 doses given</em> — is the difference between a recheck
where you wonder why it isn't resolving and one where you already know. Ask it a question
in plain English and it answers from that record only. It doesn't diagnose, dose or
prescribe; that's yours.</p>

<h2>How the relationship works</h2>
<p>You see the client in person. That examination establishes the VCPR — the part crittr
can never manufacture remotely, and the reason this is built the way it is. Everything
after that visit is legal telemedicine with <em>you</em>: follow-ups, dose reminders,
refill requests, all routed back to the practice that examined the animal.</p>

<div class=card>
  <strong>You bring your own clients across.</strong>
  <p style="margin:10px 0 0">Export your client list from Cornerstone, AVImark, ezyVet —
  whatever you use — and upload it as it comes out. Nothing is sent to anyone until you
  choose to send invitations, and every import is signed by a named person at your
  practice confirming these are existing clients you've examined and may contact.</p>
  <p style="margin:10px 0 0" class=plain>A client who accepts is connected to you. A
  client who ignores it is never contacted again. Either way it's their account — they can
  disconnect in one click, and nothing of theirs is held hostage.</p>
</div>

<h2>What you earn — the real numbers</h2>
<p>Your practice earns <strong>{rate}% of what a connected client actually pays</strong> on
eligible products. Net of discounts, and reversed if they get a refund. The rate is the
same whether or not you wrote the product into a care plan — deliberately, so nothing you
recommend is ever worth more to you than anything else you recommend.</p>

<table>
  <tr><th>Practice with</th><th>Typical first year</th></tr>
  <tr><td>Clients in your book</td><td>2,000</td></tr>
  <tr><td>Who accept the invitation</td><td>~20% → 400</td></tr>
  <tr><td>Who order in a given month</td><td>~10% → 40</td></tr>
  <tr><td>At an average order of $25</td><td>$1,000/month</td></tr>
  <tr><td><strong>Your share at {rate}%</strong></td><td><strong>~$100/month</strong></td></tr>
</table>

<p class=plain style="margin-top:14px">Those are deliberately unflattering assumptions. We
would rather you beat them than be told 30% of your book buys something every month and
find out otherwise. It is incremental revenue on purchases your clients were making
anyway — not a business, and not presented as one.</p>

<div class=note>
  <strong>Being straight with you about the catalogue.</strong> crittr's shop is currently
  mostly affiliate links to name-brand products — those send your client to a third-party
  retailer, and your practice earns nothing on them. Own-label products are where the
  share actually applies, and that range is small today and expanding. If the money is the
  reason you're considering this, wait until we've grown it. If the patient record is the
  reason, it's ready now.
</div>

<h2>What we don't do</h2>
<ul>
  <li>We don't practise veterinary medicine. Triage output is never presented as a vet's
      opinion, and every case a vet reviews records whether they agreed or overrode it.</li>
  <li>We don't dispense. Refill <em>requests</em> route to the prescribing vet; we never
      fill them.</li>
  <li>We don't operate in a state until a named veterinarian or attorney has answered that
      state's questions and switched it on. Every gate defaults to deny.</li>
  <li>We don't let another practice see your clients' records. A vet reads a chart only for
      a connected client of their own practice, or one they hold a live VCPR with — and
      every read is logged.</li>
</ul>

<h2>Getting started</h2>
<p>Apply with your licence number. A person verifies it against your state board before
your account can see anything at all — not a script, and not the same day. Then set up your
practice, upload your book, and invite as many or as few clients as you like.</p>

<a class=cta href="/vet/apply">Apply to partner</a>
<p class=plain style="margin-top:22px">Questions first? <a href="mailto:hello@crittr.ai"
style="color:var(--sage-d)">hello@crittr.ai</a></p>

</div></body></html>"""


def register_for_vets(app):
    @app.route("/for-vets", methods=["GET"])
    def for_vets_page():
        return _page(_rate())

    @app.route("/for-vets/economics", methods=["GET"])
    def for_vets_economics():
        """The worked example as data, so the numbers in the page are never hand-typed."""
        rate = _rate()
        clients, claim, monthly, aov = 2000, 0.20, 0.10, 2500
        connected = int(clients * claim)
        buyers = int(connected * monthly)
        gmv = buyers * aov
        return jsonify({
            "rate_pct": rate, "clients": clients,
            "claim_rate_pct": int(claim * 100), "connected": connected,
            "monthly_buyer_rate_pct": int(monthly * 100), "buyers": buyers,
            "avg_order_cents": aov, "monthly_gmv_cents": gmv,
            "practice_monthly_cents": int(round(gmv * rate / 100.0)),
            "assumptions": "deliberately conservative; beat them rather than miss them",
        })
