"""crittr.ai — the veterinarian-facing pages.

The API in vet_portal.py is what enforces the rules; this is what a real veterinarian
actually sees. Built so MBD can walk the whole flow end to end before asking a vet to.

DESIGN NOTE. The apply form asks for a licence number and state and then says, plainly,
that a human verifies it against the state board before the vet sees anything. That
sentence is doing real work: it sets the expectation that this is a credentialed
professional network rather than a sign-up-and-go marketplace, which is the difference
between a vet trusting it and closing the tab.
"""
from flask import request, jsonify, session

_CSS = """
<style>
  :root{--ink:#1C2A1F;--muted:#6E7D70;--line:#DFE5DB;--sage:#527E54;--sage-d:#3E6340;
        --cream:#FDFBF5;--warn:#B4541F;--er:#A32020}
  *{box-sizing:border-box}
  body{margin:0;background:var(--cream);color:var(--ink);
       font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .wrap{max-width:760px;margin:0 auto;padding:32px 20px 64px}
  .brand{font-weight:800;letter-spacing:-.02em;font-size:22px;color:var(--sage-d);
         text-decoration:none;display:inline-block;margin-bottom:28px}
  h1{font-size:30px;line-height:1.2;margin:0 0 10px;letter-spacing:-.02em}
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
  button.ghost{background:#fff;color:var(--sage-d);border:1px solid var(--line)}
  .note{background:#F2F7F1;border:1px solid #C7DEC4;border-radius:10px;padding:14px;
        font-size:14px;color:var(--sage-d);margin-top:18px}
  .msg{padding:13px;border-radius:9px;margin-top:16px;display:none;font-size:15px}
  .ok{background:#EAF5E9;border:1px solid #A6C9A2;color:#2D4A30}
  .bad{background:#FBEDEA;border:1px solid #E7C3B9;color:#8A2C10}
  .case{border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:12px;
        background:#fff}
  .pill{display:inline-block;font-size:12px;font-weight:800;letter-spacing:.04em;
        padding:4px 9px;border-radius:99px;color:#fff}
  .p-er{background:var(--er)}.p-tom{background:var(--warn)}.p-home{background:var(--sage)}
  .meta{color:var(--muted);font-size:13px;margin:8px 0}
  .empty{text-align:center;color:var(--muted);padding:34px 12px}
  code{background:#F2F1EC;padding:2px 6px;border-radius:5px;font-size:13px}
</style>
"""


def _page(title, body):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title} · crittr for veterinarians</title>{_CSS}</head><body>"
            f"<div class=wrap><a class=brand href='/'>crittr</a>{body}</div></body></html>")


APPLY_HTML = _page("Apply", """
<h1>Join crittr as a partner veterinarian</h1>
<p class=sub>Owners come to crittr at 2am. When our triage says a case needs a vet, we
want to hand it to you — with the owner's description, their photo and our reasoning
already attached, so it arrives pre-triaged rather than cold.</p>

<div class=card>
  <form id=f>
    <label>Full name and credentials</label>
    <input name=full_name placeholder="Jane Smith, DVM" required>

    <label>Clinic or practice</label>
    <input name=clinic_name placeholder="Sapillo Animal Hospital">

    <div class=row>
      <div>
        <label>Licence state</label>
        <select name=state required>
          <option value="">Select…</option>
          <option>NM</option><option>AZ</option><option>TX</option><option>CO</option>
          <option>UT</option><option>OK</option><option>NV</option><option>CA</option>
        </select>
      </div>
      <div>
        <label>Licence number</label>
        <input name=license_number placeholder="12345" required>
      </div>
    </div>

    <div class=row>
      <div><label>Licence expires</label><input name=expires_on type=date></div>
      <div><label>Phone</label><input name=phone placeholder="575-555-0100"></div>
    </div>

    <label>Email</label>
    <input name=email type=email placeholder="jane@clinic.com">

    <button type=submit>Apply to partner</button>
  </form>
  <div id=msg class=msg></div>
</div>

<div class=note>
  <strong>What happens next.</strong> We verify your licence number against your state
  board — a person does this, not a script — before your account can see a single case.
  You'll only ever receive cases in a state you're licensed in. You set your own hours,
  capacity and fees; crittr does not practise medicine and does not set your prices.
</div>

<script>
document.getElementById('f').addEventListener('submit', async function(e){
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target).entries());
  const m = document.getElementById('msg');
  m.style.display='block'; m.className='msg'; m.textContent='Sending…';
  try{
    const r = await fetch('/api/vet/apply',{method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(d)});
    const j = await r.json();
    if(r.ok){
      m.className='msg ok';
      m.innerHTML = '<strong>Application received.</strong> ' + (j.next||'') +
        ' You can check your status any time at <code>/vet</code>.';
      e.target.reset();
    } else {
      m.className='msg bad';
      m.textContent = j.error || ('Something went wrong (HTTP '+r.status+')');
    }
  }catch(err){ m.className='msg bad'; m.textContent='Network error: '+err; }
});
</script>
""")


CONSOLE_HTML = _page("Case queue", """
<h1>Your case queue</h1>
<p class=sub id=sub>Loading…</p>
<div id=out></div>
<script>
function pill(v){
  const c = v==='ER NOW' ? 'p-er' : (v==='VET TOMORROW' ? 'p-tom' : 'p-home');
  return '<span class="pill '+c+'">'+(v||'—')+'</span>';
}
async function load(){
  const out=document.getElementById('out'), sub=document.getElementById('sub');
  const me = await (await fetch('/api/vet/me')).json().catch(()=>null);
  if(!me || me.error){
    sub.textContent='';
    out.innerHTML='<div class=card><strong>You are not signed in as a partner '+
      'veterinarian.</strong><p class=meta>'+((me&&me.error)||'')+'</p>'+
      '<a href="/vet/apply"><button>Apply to partner</button></a></div>';
    return;
  }
  const states=(me.active_states||[]);
  sub.innerHTML = states.length
    ? 'Licensed and active in <strong>'+states.join(', ')+'</strong>.'
    : 'Your account is not yet cleared to receive cases.';
  const r = await (await fetch('/api/vet/cases')).json();
  if(!r.cases || !r.cases.length){
    out.innerHTML='<div class="card empty"><strong>No cases waiting.</strong>'+
      '<p class=meta>'+(r.note||'Cases appear here when crittr triages an owner in '+
      'your state and the case needs a veterinarian.')+'</p></div>';
    return;
  }
  out.innerHTML = r.cases.map(function(c){
    return '<div class=case>'+pill(c.ai_verdict)+
      ' <span class=meta>'+c.state+' · case #'+c.id+'</span>'+
      '<p><strong>Owner says:</strong> '+(c.owner_message||'—')+'</p>'+
      (c.ai_reasoning? '<p class=meta><strong>crittr reasoning:</strong> '+c.ai_reasoning+'</p>':'')+
      '<button onclick="claim('+c.id+')">Claim this case</button></div>';
  }).join('');
}
async function claim(id){
  const r = await fetch('/api/vet/cases/'+id+'/claim',{method:'POST'});
  const j = await r.json();
  alert(r.ok ? 'Claimed. The owner\\'s details are yours to action.' : (j.error||'Could not claim'));
  load();
}
load();
</script>
""")


def register_vet_pages(app):
    """Wire the vet-facing pages. API stays the enforcement layer; these are the doors."""

    @app.route("/vet/apply", methods=["GET"])
    def vet_apply_page():
        return APPLY_HTML

    @app.route("/vet", methods=["GET"])
    def vet_console_page():
        return CONSOLE_HTML
