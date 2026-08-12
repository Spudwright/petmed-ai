"""crittr.ai — the operator's screens for running the vet rollout.

Everything the rollout needs already existed as API routes, which meant running it took
curl and a copy of the admin password in your shell history. That is fine for one test and
wrong for a process you will repeat once per state and once per veterinarian.

TWO SCREENS:

  /admin/states      the state rollout. Shows the six questions for a state, what is
                     currently blocking each action, and a form to record a named human's
                     answers and switch it on.
  /admin/vets        applications waiting, their licence details, and a verify button.

THE FORM IS THE MEETING AGENDA. The questions on /admin/states are exactly what a
veterinarian or attorney has to answer, in the words you would actually ask them, so the
screen can be open during the call and filled in as they speak. That is the point: the
compliance record should be written WHILE the person is on the phone, not reconstructed
from memory afterwards.

Protected by the same HTTP basic auth as the rest of the admin surface, which fails closed
when ADMIN_USER / ADMIN_PASS are unset.
"""
from flask import request, jsonify

_CSS = """
<style>
  :root{--ink:#1C2A1F;--muted:#6E7D70;--line:#DFE5DB;--sage:#527E54;--sage-d:#3E6340;
        --cream:#FDFBF5;--red:#A32020;--amber:#B4541F}
  *{box-sizing:border-box}
  body{margin:0;background:var(--cream);color:var(--ink);
       font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .wrap{max-width:900px;margin:0 auto;padding:26px 20px 70px}
  a.brand{font-weight:800;font-size:20px;color:var(--sage-d);text-decoration:none}
  nav{margin:14px 0 26px} nav a{color:var(--sage-d);margin-right:16px}
  h1{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}
  .sub{color:var(--muted);margin:0 0 22px}
  .card{background:#fff;border:1px solid var(--line);border-radius:13px;padding:20px;
        margin-bottom:16px}
  label{display:block;font-weight:600;font-size:14px;margin:16px 0 5px}
  .q{color:var(--muted);font-weight:400;font-size:13px;margin-bottom:6px}
  input,select{width:100%;padding:10px 11px;border:1px solid var(--line);border-radius:8px;
       font:inherit;background:#fff}
  .row{display:flex;gap:10px;align-items:center}
  button{background:var(--sage);color:#fff;border:0;border-radius:9px;padding:11px 18px;
         font:inherit;font-weight:700;cursor:pointer;margin-top:18px}
  button:hover{background:var(--sage-d)}
  button.warn{background:var(--amber)}
  .pill{display:inline-block;font-size:11px;font-weight:800;letter-spacing:.05em;
        padding:4px 9px;border-radius:99px;color:#fff;text-transform:uppercase}
  .p-on{background:var(--sage)} .p-off{background:var(--muted)} .p-sus{background:var(--red)}
  table{width:100%;border-collapse:collapse;margin-top:8px}
  th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);font-size:14px}
  th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase}
  .yes{color:var(--sage-d);font-weight:700} .no{color:var(--red);font-weight:700}
  .msg{padding:12px;border-radius:9px;margin-top:14px;display:none}
  .ok{background:#EAF5E9;border:1px solid #A6C9A2;color:#2D4A30}
  .bad{background:#FBEDEA;border:1px solid #E7C3B9;color:#8A2C10}
  .hint{background:#F2F7F1;border:1px solid #C7DEC4;border-radius:9px;padding:12px;
        font-size:13px;color:var(--sage-d);margin-top:14px}
</style>
"""


def _page(title, body):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title} · crittr admin</title>{_CSS}</head><body><div class=wrap>"
            "<a class=brand href='/'>crittr</a>"
            "<nav><a href='/admin/states'>State rollout</a>"
            "<a href='/admin/vets'>Veterinarians</a></nav>"
            f"{body}</div></body></html>")


STATES_HTML = _page("State rollout", """
<h1>State rollout</h1>
<p class=sub>A state is closed until a named person answers these. Keep this open during
the call and fill it in as they speak — the record should be written while they're on the
phone, not reconstructed afterwards.</p>

<div class=card>
  <div class=row>
    <input id=st placeholder="NM" maxlength=2 style="width:90px;text-transform:uppercase">
    <button onclick="load()" style="margin-top:0">Load state</button>
    <span id=status></span>
  </div>
</div>

<div id=out></div>

<script>
const QS = [
 ["telemedicine_vcpr_allowed","Can a valid VCPR be established by telemedicine alone here, or is an in-person exam required first?"],
 ["routing_allowed","May we route an owner's triage case to a veterinarian licensed here?"],
 ["rx_allowed","May a vet prescribe on the basis of a telemedicine consult where a VCPR exists?"],
 ["vcpr_required_for_rx_satisfied","Does our VCPR record satisfy this state's requirement for prescribing?"],
 ["controlled_allowed","May controlled substances be prescribed via telemedicine here?"]
];
function esc(s){return (s||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));}
async function load(){
  const st=(document.getElementById('st').value||'').toUpperCase().slice(0,2);
  if(st.length!==2){ alert('Two-letter state code'); return; }
  const r=await fetch('/api/admin/compliance/'+st);
  if(!r.ok){ document.getElementById('out').innerHTML='<div class="card">Could not load ('+r.status+')</div>'; return; }
  const j=await r.json();
  const cls = j.status==='active'?'p-on':(j.status==='suspended'?'p-sus':'p-off');
  document.getElementById('status').innerHTML='<span class="pill '+cls+'">'+j.status+'</span>';
  const acts = Object.entries(j.actions||{}).map(([k,v])=>
    '<tr><td>'+k+'</td><td class="'+(v.allowed?'yes':'no')+'">'+(v.allowed?'ALLOWED':'DENIED')+
    '</td><td style="color:#6E7D70">'+esc((v.reason||'').slice(0,90))+'</td></tr>').join('');
  const fields = QS.map(([k,q])=>
    '<label>'+k.replace(/_/g,' ')+'<div class=q>'+q+'</div>'+
    '<select name="'+k+'"><option value="">— not answered —</option>'+
    '<option value="yes">Yes</option><option value="no">No</option></select></label>').join('');
  document.getElementById('out').innerHTML =
    '<div class=card><h3 style="margin:0 0 6px">Where '+st+' stands</h3>'+
    '<table><tr><th>Action</th><th>Status</th><th>Why</th></tr>'+acts+'</table>'+
    (j.confirmed_by? '<p class=q>Last confirmed by '+esc(j.confirmed_by)+' on '+esc((j.confirmed_at||'').slice(0,10))+'</p>':'')+
    '</div>'+
    '<div class=card><h3 style="margin:0 0 6px">Record answers and activate</h3>'+
    '<form id=f>'+fields+
    '<label>VCPR valid for (days)<div class=q>Blank if the state does not expire it.</div>'+
    '<input name=vcpr_valid_days placeholder="365"></label>'+
    '<label>Who determined this?<div class=q>Required. Name and credentials — a compliance decision with no name on it is not a decision.</div>'+
    '<input name=actor placeholder="Dr Jane Smith DVM, NM licence 12345" required></label>'+
    '<label>Note<input name=note placeholder="confirmed by phone 2026-08-12"></label>'+
    '<button type=submit>Activate '+st+'</button>'+
    '<button type=button class=warn onclick="suspend(\\''+st+'\\')">Suspend</button>'+
    '</form><div id=msg class=msg></div>'+
    '<div class=hint>Answering only <strong>routing</strong> yes is a perfectly good launch: '+
    'it makes crittr a triage-and-booking platform. Prescribing can stay off.</div></div>';
  document.getElementById('f').onsubmit = async function(e){
    e.preventDefault();
    const fd=new FormData(e.target), answers={};
    QS.forEach(([k])=>{ const v=fd.get(k); if(v) answers[k]=(v==='yes'); });
    const d=fd.get('vcpr_valid_days'); if(d) answers['vcpr_valid_days']=parseInt(d);
    const m=document.getElementById('msg'); m.style.display='block'; m.className='msg';
    m.textContent='Saving…';
    const res=await fetch('/api/admin/compliance/'+st+'/activate',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({actor:fd.get('actor'),note:fd.get('note'),answers:answers})});
    const jj=await res.json();
    if(res.ok){ m.className='msg ok'; m.textContent=st+' updated.'; load(); }
    else { m.className='msg bad'; m.textContent=jj.error||'Failed'; }
  };
}
async function suspend(st){
  if(!confirm('Suspend '+st+'? Routing stops there immediately.')) return;
  await fetch('/api/admin/compliance/'+st+'/suspend',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({actor:'admin',reason:'suspended from admin'})});
  load();
}
document.getElementById('st').value='NM'; load();
</script>
""")


VETS_HTML = _page("Veterinarians", """
<h1>Veterinarians</h1>
<p class=sub>Verify a licence against the state board yourself, then record it here. A
person does this — we do not scrape a board and call it verified.</p>
<div id=out class=card>Loading…</div>
<script>
function esc(s){return (s||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));}
async function load(){
  const r=await fetch('/api/admin/vets');
  const j=await r.json();
  const vets=j.vets||[];
  if(!vets.length){ document.getElementById('out').innerHTML=
    '<strong>No applications yet.</strong><p class=q>They arrive from /vet/apply.</p>'; return; }
  document.getElementById('out').innerHTML='<table><tr><th>Name</th><th>Clinic</th>'+
    '<th>Email</th><th>Status</th><th></th></tr>'+
    vets.map(function(v){
      const verified = v.status==='verified';
      return '<tr><td>'+esc(v.full_name)+'</td><td>'+esc(v.clinic_name||'—')+'</td>'+
        '<td>'+esc(v.email||'—')+'</td>'+
        '<td><span class="pill '+(verified?'p-on':'p-off')+'">'+esc(v.status)+'</span></td>'+
        '<td>'+(verified?'<span class=q>by '+esc(v.verified_by||'')+'</span>'
                       :'<button style="margin:0;padding:7px 12px" onclick="verify('+v.id+')">Verify</button>')+
        '</td></tr>';
    }).join('')+'</table>';
}
async function verify(id){
  const who=prompt('Your name, for the record:'); if(!who) return;
  const note=prompt('How did you verify it? (e.g. checked NM board register)')||'';
  const r=await fetch('/api/admin/vets/'+id+'/verify',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({actor:who,notes:note})});
  if(!r.ok){ alert('Failed'); return; }
  load();
}
load();
</script>
""")


def register_vet_admin_pages(app, admin_required):
    @app.route("/admin/states", methods=["GET"])
    @admin_required
    def admin_states_page():
        return STATES_HTML

    @app.route("/admin/vets", methods=["GET"])
    @admin_required
    def admin_vets_page():
        return VETS_HTML
