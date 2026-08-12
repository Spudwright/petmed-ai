"""crittr.ai — the screens for bringing a practice's client book across.

Three doors, in the order a clinic walks through them:

  /vet/practice        set up the practice, upload the book, see the funnel and the money
  /vet/claim/<token>   what the OWNER sees when they click the invitation
  /account/practice    where an owner disconnects, because invariant 3 needs a door

DESIGN NOTE ON THE UPLOAD SCREEN. The attestation is a typed name, not a ticked box. A
checkbox is something a person clicks past; typing your own name is a moment where you
consider whether the sentence above it is true. That is the entire defence between this
feature and a clinic uploading a list it had no right to upload, so it is deliberately the
most prominent thing on the page rather than small print at the bottom.
"""
from flask import request, session

_CSS = """
<style>
  :root{--ink:#1C2A1F;--muted:#6E7D70;--line:#DFE5DB;--sage:#527E54;--sage-d:#3E6340;
        --cream:#FDFBF5;--warn:#B4541F;--er:#A32020}
  *{box-sizing:border-box}
  body{margin:0;background:var(--cream);color:var(--ink);
       font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .wrap{max-width:780px;margin:0 auto;padding:32px 20px 64px}
  .brand{font-weight:800;letter-spacing:-.02em;font-size:22px;color:var(--sage-d);
         text-decoration:none;display:inline-block;margin-bottom:28px}
  h1{font-size:30px;line-height:1.2;margin:0 0 10px;letter-spacing:-.02em}
  h2{font-size:19px;margin:0 0 14px}
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
  .attest{background:#FFFBF0;border:1px solid #E8D9A8;border-radius:10px;padding:16px;
          margin-top:18px}
  .msg{padding:13px;border-radius:9px;margin-top:16px;display:none;font-size:15px}
  .ok{background:#EAF5E9;border:1px solid #A6C9A2;color:#2D4A30}
  .bad{background:#FBEDEA;border:1px solid #E7C3B9;color:#8A2C10}
  .meta{color:var(--muted);font-size:13px;margin:8px 0}
  .stats{display:flex;gap:12px;flex-wrap:wrap;margin:4px 0 0}
  .stat{flex:1;min-width:130px;border:1px solid var(--line);border-radius:12px;
        padding:14px;background:#fff}
  .stat b{display:block;font-size:26px;letter-spacing:-.02em}
  .stat span{color:var(--muted);font-size:13px}
  table{width:100%;border-collapse:collapse;font-size:14px;margin-top:14px}
  th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;
     text-transform:uppercase;letter-spacing:.04em;padding:6px 8px}
  td{padding:8px;border-top:1px solid var(--line)}
  .tag{font-size:11px;font-weight:800;padding:3px 8px;border-radius:99px;
       background:#EEF2ED;color:var(--sage-d)}
  .tag.claimed{background:#EAF5E9;color:#2D4A30}
  .tag.invited{background:#FFF6E5;color:#8A5A10}
  .tag.declined{background:#F4F1F1;color:#8A7C7C}
  code{background:#F2F1EC;padding:2px 6px;border-radius:5px;font-size:13px}
  .empty{text-align:center;color:var(--muted);padding:34px 12px}
</style>
"""


def _page(title, body):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title} · crittr</title>{_CSS}</head><body>"
            f"<div class=wrap><a class=brand href='/'>crittr</a>{body}</div></body></html>")


PRACTICE_HTML = _page("Your practice", r"""
<h1>Your practice on crittr</h1>
<p class=sub>Your clients already buy food, supplements and preventatives every month. This
is how that stops going to Chewy — you invite the clients you already see, and the margin
comes back here.</p>

<div id=setup class=card>
  <h2>1 · Your practice</h2>
  <form id=pf>
    <label>Practice name</label>
    <input name=name placeholder="Sapillo Animal Hospital" required>
    <div class=row>
      <div><label>State</label>
        <select name=state>
          <option value="">Select…</option>
          <option>NM</option><option>AZ</option><option>TX</option><option>CO</option>
          <option>UT</option><option>OK</option><option>NV</option><option>CA</option>
        </select></div>
      <div><label>Phone</label><input name=phone placeholder="575-555-0100"></div>
    </div>
    <label>Contact email <span class=meta>— where your clients' replies go</span></label>
    <input name=contact_email type=email placeholder="front.desk@clinic.com">
    <button type=submit>Save practice</button>
  </form>
  <div id=pmsg class=msg></div>
</div>

<div id=book style=display:none>
  <div class=card>
    <h2>2 · Your client book</h2>
    <p class=meta>Export your client list from Cornerstone, AVImark, ezyVet — whatever you
    use — and drop the CSV in as it comes out. We'll find the email, owner, pet and last-seen
    columns ourselves.</p>
    <form id=uf>
      <label>Client list (CSV)</label>
      <input type=file name=file accept=".csv,text/csv" required>

      <div class=attest>
        <strong>Before you upload.</strong>
        <p style="margin:8px 0 0;font-size:14px">I confirm that every person in this file is
        an existing client of this practice, that we have examined their animal in person,
        and that we have their permission to contact them about their pet's care.</p>
        <label>Type your name to confirm</label>
        <input name=attested_by placeholder="Jane Smith, DVM" required>
      </div>

      <button type=submit>Upload the book</button>
    </form>
    <div id=umsg class=msg></div>
    <div class=note><strong>Nothing is sent by uploading.</strong> The list lands in your
    book and stays there. You choose when invitations go out, in the next step.</div>
  </div>

  <div class=card>
    <h2>3 · Invitations</h2>
    <div class=stats id=stats></div>
    <button id=inv class=ghost>Send invitations to everyone not yet invited</button>
    <div id=imsg class=msg></div>
    <div id=clients></div>
  </div>

  <div class=card>
    <h2>4 · What you've earned</h2>
    <div class=stats id=earn></div>
    <p class=meta id=erate></p>
  </div>
</div>

<script>
function money(c){ return '$' + ((c||0)/100).toFixed(2); }
function el(id){ return document.getElementById(id); }

async function load(){
  const r = await fetch('/api/vet/practice');
  if(r.status===403 || r.status===401){
    el('setup').innerHTML = '<h2>Not signed in as a partner veterinarian</h2>' +
      '<p class=meta>This page is for verified partner vets.</p>' +
      '<a href="/vet/apply"><button>Apply to partner</button></a>';
    return;
  }
  const j = await r.json();
  if(!j.practice) return;
  const f = el('pf');
  f.name.value = j.practice.name || '';
  if(j.practice.state) f.state.value = j.practice.state;
  f.phone.value = j.practice.phone || '';
  f.contact_email.value = j.practice.contact_email || '';
  el('book').style.display='block';
  renderBook(j.book); renderEarnings(j.practice, j.earnings);
  loadClients();
}

function renderBook(b){
  if(!b){ el('stats').innerHTML=''; return; }
  const c = b.counts||{};
  el('stats').innerHTML =
    stat(b.total||0,'in your book') +
    stat(c.imported||0,'not yet invited') +
    stat(c.invited||0,'invited, waiting') +
    stat(c.claimed||0,'connected') +
    (b.claim_rate_pct!=null ? stat(b.claim_rate_pct+'%','accepted') : '');
}
function stat(v,l){ return '<div class=stat><b>'+v+'</b><span>'+l+'</span></div>'; }

function renderEarnings(p, e){
  if(!e){ return; }
  const by = e.by_source||{};
  el('earn').innerHTML =
    stat(money(e.total_cents),'last 30 days') +
    stat(money((by.plan||{}).cents),'from care plans') +
    stat(money((by.practice||{}).cents),'from your clients');
  el('erate').textContent =
    'Care-plan sales pay more than relationship sales: your practice rate is ' +
    (p.rev_share_pct||8) + '% on a connected client\'s order, and more when you wrote the ' +
    'product into a plan. Rates are frozen on each order at the time of sale.';
}

async function loadClients(){
  const j = await (await fetch('/api/vet/practice/clients')).json();
  const rows = j.clients||[];
  if(!rows.length){ el('clients').innerHTML =
    '<div class=empty>Your book is empty — upload a CSV above.</div>'; return; }
  el('clients').innerHTML = '<table><tr><th>Owner</th><th>Pet</th><th>Email</th>' +
    '<th>Status</th></tr>' + rows.slice(0,200).map(function(c){
      return '<tr><td>'+(c.owner_name||'—')+'</td><td>'+(c.pet_name||'—')+'</td>'+
        '<td class=meta>'+c.email+'</td>'+
        '<td><span class="tag '+c.status+'">'+c.status+'</span></td></tr>';
    }).join('') + '</table>' +
    (rows.length>200 ? '<p class=meta>Showing 200 of '+rows.length+'.</p>' : '');
}

el('pf').addEventListener('submit', async function(e){
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target).entries());
  const m = el('pmsg'); m.style.display='block'; m.className='msg'; m.textContent='Saving…';
  const r = await fetch('/api/vet/practice',{method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify(d)});
  const j = await r.json();
  m.className = 'msg ' + (r.ok?'ok':'bad');
  m.textContent = r.ok ? 'Practice saved.' : (j.error||'Could not save');
  if(r.ok){ el('book').style.display='block'; load(); }
});

el('uf').addEventListener('submit', async function(e){
  e.preventDefault();
  const m = el('umsg'); m.style.display='block'; m.className='msg';
  m.textContent='Reading your file…';
  const r = await fetch('/api/vet/practice/import',{method:'POST',
    body:new FormData(e.target)});
  const j = await r.json();
  if(r.ok){
    m.className='msg ok';
    m.innerHTML = '<strong>'+j.added+' clients added</strong> to your book' +
      (j.skipped ? ' · '+j.skipped+' were already there' : '') +
      (j.problem_count ? ' · <span class=meta>'+j.problem_count+' rows had no usable '+
        'email: '+(j.problems||[]).slice(0,3).join('; ')+'</span>' : '') +
      '<p style="margin:8px 0 0">'+(j.next||'')+'</p>';
    e.target.reset(); loadClients(); load();
  } else {
    m.className='msg bad';
    m.innerHTML = (j.error||'Upload failed') +
      (j.problems ? '<p class=meta>'+j.problems.join('<br>')+'</p>' : '');
  }
});

el('inv').addEventListener('click', async function(){
  if(!confirm('Send an invitation email to every client in your book who has not been '+
              'invited yet?')) return;
  const b = el('inv'); b.disabled = true; b.textContent = 'Sending…';
  const m = el('imsg'); m.style.display='block'; m.className='msg';
  m.textContent='Sending…';
  const r = await fetch('/api/vet/practice/invite',{method:'POST',
    headers:{'Content-Type':'application/json'}, body:'{}'});
  const j = await r.json();
  m.className='msg ' + (r.ok?'ok':'bad');
  m.textContent = r.ok
    ? (j.sent+' invitations sent' + (j.failed ? ', '+j.failed+' could not be delivered' : '')
       + '.')
    : (j.error||'Could not send');
  b.disabled = false; b.textContent = 'Send invitations to everyone not yet invited';
  loadClients(); load();
});

load();
</script>
""")


def _claim_page(practice_name, pet_name, token, signed_in):
    who = f" for {pet_name}" if pet_name else ""
    cta = ("Connect my account" if signed_in
           else "Create an account to connect")
    return _page("Connect with your vet", f"""
<h1>{practice_name} would like to look after {pet_name or 'your pet'} online</h1>
<p class=sub>You've already seen them in person. This connects that same practice to your
crittr account, so the food, supplements and refills they recommend{who} come to you —
with reminders — and your practice stays part of the care.</p>

<div class=card>
  <h2>What connecting does</h2>
  <ul style="margin:0;padding-left:20px;color:#3E6340">
    <li>Your vet's recommendations show up as a plan you can order from in one tap.</li>
    <li>Dose reminders and refill requests go to the practice that knows your animal.</li>
    <li>Your practice earns on what you order, instead of that going to a warehouse.</li>
  </ul>
  <div class=note><strong>It's your account either way.</strong> Your pets, your orders and
  your history stay yours, and you can disconnect from
  <code>/account/practice</code> at any time — no email, no phone call.</div>
  <button id=go>{cta}</button>
  <button id=no class=ghost>No thanks</button>
  <div id=msg class=msg></div>
</div>

<script>
const TOKEN = {token!r};
document.getElementById('go').addEventListener('click', async function(){{
  const m = document.getElementById('msg');
  m.style.display='block'; m.className='msg'; m.textContent='Connecting…';
  const r = await fetch('/api/practice/claim',{{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{token:TOKEN}})}});
  const j = await r.json();
  if(r.ok){{ m.className='msg ok';
    m.innerHTML='<strong>Connected.</strong> Your practice will appear on your care page.';
  }} else if(j.needs_auth){{
    sessionStorage.setItem('crittr_claim_token', TOKEN);
    location.href = '/signup?next=' + encodeURIComponent('/vet/claim/' + TOKEN);
  }} else {{ m.className='msg bad'; m.textContent = j.error || 'Could not connect'; }}
}});
document.getElementById('no').addEventListener('click', async function(){{
  const m = document.getElementById('msg');
  await fetch('/api/practice/decline',{{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{token:TOKEN}})}});
  m.style.display='block'; m.className='msg ok';
  m.textContent='No problem — we won\\'t send another. Nothing about your account changed.';
}});
</script>
""")


ACCOUNT_HTML = _page("Your practice", """
<h1>Your veterinary practice</h1>
<p class=sub id=sub>Loading…</p>
<div class=card id=box></div>
<script>
async function load(){
  const j = await (await fetch('/api/practice/me')).json();
  const sub=document.getElementById('sub'), box=document.getElementById('box');
  if(!j.connected){
    sub.textContent='';
    box.innerHTML='<div class=empty>You are not connected to a veterinary practice.</div>';
    return;
  }
  sub.innerHTML = 'You are connected to <strong>'+j.practice+'</strong>.';
  box.innerHTML = '<p>They can send you care plans, reminders and refills, and they earn '+
    'on what you order here.</p>'+
    '<button class=ghost id=rel>Disconnect from '+j.practice+'</button>'+
    '<div id=m class=msg></div>';
  document.getElementById('rel').addEventListener('click', async function(){
    if(!confirm('Disconnect? Your pets, orders and history all stay with you.')) return;
    const r = await fetch('/api/practice/release',{method:'POST'});
    const m = document.getElementById('m');
    m.style.display='block'; m.className='msg ' + (r.ok?'ok':'bad');
    m.textContent = r.ok ? 'Disconnected.' : 'Could not disconnect.';
    if(r.ok) load();
  });
}
load();
</script>
""")


def register_practice_pages(app, q1):
    import vet_practice as vpr

    @app.route("/vet/practice", methods=["GET"])
    def vet_practice_page():
        return PRACTICE_HTML

    @app.route("/vet/claim/<token>", methods=["GET"])
    def vet_claim_page(token):
        c = vpr.client_for_token(q1, token)
        if not c:
            return _page("Invitation", """
                <h1>That invitation link isn't valid</h1>
                <p class=sub>It may already have been used, or declined. If you think it
                should work, ask your veterinary practice to send a new one.</p>"""), 404
        return _claim_page(c.get("practice_name") or "Your veterinary practice",
                           c.get("pet_name"), token, bool(session.get("user_id")))

    @app.route("/account/practice", methods=["GET"])
    def account_practice_page():
        return ACCOUNT_HTML
