"""crittr.ai — the owner-facing aftercare screens.

These are where the money is. The API in vet_aftercare.py holds the rules; this is what an
owner opens on their phone at 8am with a dog that needs a pill.

THREE SCREENS, and the order matters:

  /care                 today's doses first, then the plan. Whatever is due RIGHT NOW is
                        the only thing most people open the app for.
  /care/plan/<id>       the vet's plan, with the vet's name on it and buy buttons against
                        the things they recommended.
  /care/followup/<id>   "how's he doing?" — a message, a photo, back to the same vet.

THE DESIGN RULE THROUGHOUT: attribute everything to the veterinarian, never to crittr.
"Dr Smith recommended this" converts and "recommended for your pet" does not, because the
first is a professional's instruction and the second is an advert. It is also simply true,
which is why it is safe to lean on.
"""
from flask import session

_CSS = """
<style>
  :root{--ink:#1C2A1F;--muted:#6E7D70;--line:#DFE5DB;--sage:#527E54;--sage-d:#3E6340;
        --cream:#FDFBF5;--amber:#B4541F}
  *{box-sizing:border-box}
  body{margin:0;background:var(--cream);color:var(--ink);
       font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .wrap{max-width:680px;margin:0 auto;padding:26px 18px 70px}
  .brand{font-weight:800;font-size:21px;color:var(--sage-d);text-decoration:none;
         display:inline-block;margin-bottom:22px;letter-spacing:-.02em}
  h1{font-size:27px;margin:0 0 6px;letter-spacing:-.02em}
  h2{font-size:17px;margin:30px 0 12px;letter-spacing:-.01em}
  .sub{color:var(--muted);margin:0 0 22px}
  .card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;
        margin-bottom:12px}
  .dose{display:flex;align-items:center;gap:14px}
  .dose .t{flex:1}
  .dose .when{color:var(--muted);font-size:13px}
  .tick{background:var(--sage);color:#fff;border:0;border-radius:10px;padding:11px 16px;
        font:inherit;font-weight:700;cursor:pointer;white-space:nowrap}
  .tick:hover{background:var(--sage-d)}
  .tick.done{background:#E4EFE2;color:var(--sage-d);cursor:default}
  .skip{background:#fff;border:1px solid var(--line);color:var(--muted);border-radius:10px;
        padding:11px 13px;font:inherit;cursor:pointer}
  .byvet{background:#F2F7F1;border:1px solid #C7DEC4;border-radius:10px;padding:12px 14px;
         font-size:14px;color:var(--sage-d);margin-bottom:18px}
  .item{display:flex;gap:14px;align-items:flex-start}
  .item img{width:64px;height:64px;object-fit:cover;border-radius:10px;background:#F2F1EC}
  .item .b{flex:1}
  .kind{display:inline-block;font-size:11px;font-weight:800;letter-spacing:.05em;
        text-transform:uppercase;color:var(--muted);margin-bottom:3px}
  .price{font-weight:800;margin-top:6px}
  .buy{background:var(--sage);color:#fff;border:0;border-radius:9px;padding:9px 15px;
       font:inherit;font-weight:700;cursor:pointer;margin-top:8px}
  .buy:hover{background:var(--sage-d)}
  textarea{width:100%;min-height:110px;padding:12px;border:1px solid var(--line);
           border-radius:10px;font:inherit}
  button.primary{background:var(--sage);color:#fff;border:0;border-radius:10px;
        padding:13px 20px;font:inherit;font-weight:700;cursor:pointer;margin-top:14px}
  .empty{text-align:center;color:var(--muted);padding:30px 12px}
  .msg{padding:12px;border-radius:9px;margin-top:14px;display:none;font-size:15px}
  .ok{background:#EAF5E9;border:1px solid #A6C9A2;color:#2D4A30}
  .bad{background:#FBEDEA;border:1px solid #E7C3B9;color:#8A2C10}
  a.plain{color:var(--sage-d)}
</style>
"""


def _page(title, body):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title} · crittr</title>{_CSS}</head><body>"
            f"<div class=wrap><a class=brand href='/'>crittr</a>{body}</div></body></html>")


CARE_HTML = _page("Care", """
<h1>Care</h1>
<p class=sub id=sub>Loading…</p>
<div id=doses></div>
<h2 id=ph style=display:none>Your plans</h2>
<div id=plans></div>
<script>
function esc(s){return (s||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));}
async function load(){
  const sub=document.getElementById('sub');
  const dosesEl=document.getElementById('doses'), plansEl=document.getElementById('plans');
  const d = await (await fetch('/api/care/doses')).json();
  if(d.error){ sub.innerHTML='<a class=plain href="/login">Sign in</a> to see your pet\\'s care plan.'; return; }
  const doses = d.doses||[];
  sub.textContent = doses.length
    ? doses.length+' thing'+(doses.length>1?'s':'')+' to give today.'
    : 'Nothing due right now.';
  dosesEl.innerHTML = doses.length ? doses.map(function(x){
      return '<div class="card dose" id="d'+x.id+'">'+
        '<div class=t><strong>'+esc(x.title)+'</strong>'+
        (x.instructions?'<div class=when>'+esc(x.instructions)+'</div>':'')+
        '<div class=when>Due '+ (x.due_at||'').slice(0,16).replace('T',' ') +'</div></div>'+
        '<button class=tick onclick="give('+x.id+')">Given</button>'+
        '<button class=skip onclick="skip('+x.id+')">Skip</button></div>';
    }).join('')
    : '<div class="card empty">No medication due. Anything your vet prescribes will '+
      'appear here with a reminder.</div>';
  const p = await (await fetch('/api/care/plans')).json();
  const plans = p.plans||[];
  if(plans.length){
    document.getElementById('ph').style.display='block';
    plansEl.innerHTML = plans.map(function(pl){
      return '<div class=card><strong>'+esc(pl.summary||'Care plan')+'</strong>'+
        '<div class=when>From '+esc(pl.vet_name||'your vet')+
        (pl.clinic_name? ' · '+esc(pl.clinic_name):'')+'</div>'+
        '<button class=buy onclick="location.href=\\'/care/plan/'+pl.id+'\\'">Open plan</button>'+
        '</div>';
    }).join('');
  }
}
async function give(id){ await mark(id,true); }
async function skip(id){ await mark(id,false); }
async function mark(id,given){
  const r = await fetch('/api/care/doses/'+id,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({given:given})});
  if(r.ok){ const el=document.getElementById('d'+id); if(el) el.remove(); load(); }
}
load();
</script>
""")


PLAN_HTML = _page("Your plan", """
<h1 id=title>Care plan</h1>
<div class=byvet id=byvet>Loading…</div>
<div id=items></div>
<script>
function esc(s){return (s||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));}
const KIND={medication:'Medication',give:'Give daily',feed:'Feed',recheck:'Recheck'};
async function load(){
  const id = location.pathname.split('/').pop();
  const r = await fetch('/api/care/plans/'+id);
  const j = await r.json();
  if(!r.ok){ document.getElementById('byvet').textContent = j.error||'Not available'; return; }
  const plan=j.plan, items=j.items||[];
  document.getElementById('title').textContent = plan.summary || 'Care plan';
  document.getElementById('byvet').innerHTML =
    '<strong>Recommended by your veterinarian.</strong> These are the instructions from '+
    'the vet who examined your pet — not suggestions from crittr.';
  document.getElementById('items').innerHTML = items.map(function(it){
    const hasProduct = it.product_id && it.product_id!=='None';
    const price = it.price_cents && it.price_cents!=='None'
      ? '$'+(parseInt(it.price_cents)/100).toFixed(2) : '';
    return '<div class=card><div class=item>'+
      (hasProduct && it.image_url? '<img src="'+esc(it.image_url)+'" alt="">':'')+
      '<div class=b><span class=kind>'+(KIND[it.kind]||esc(it.kind))+'</span>'+
      '<div><strong>'+esc(it.title)+'</strong></div>'+
      (it.instructions? '<div style="color:#6E7D70;font-size:14px">'+esc(it.instructions)+'</div>':'')+
      (it.times_per_day && it.times_per_day!=='None'
        ? '<div style="color:#6E7D70;font-size:14px">'+it.times_per_day+'x daily for '+
          it.days+' days</div>' : '')+
      (it.due_on && it.due_on!=='None'
        ? '<div style="color:#B4541F;font-size:14px">Due '+it.due_on+'</div>':'')+
      (price? '<div class=price>'+price+'</div>':'')+
      (hasProduct? '<button class=buy onclick="buy(\\''+esc(it.slug||'')+'\\')">Add to cart</button>':'')+
      '</div></div></div>';
  }).join('');
}
function buy(slug){ location.href = slug ? '/shop/'+slug : '/shop'; }
load();
</script>
""")


FOLLOWUP_HTML = _page("Follow-up", """
<h1>How's your pet doing?</h1>
<p class=sub>Your vet asked to check in. A sentence or two is plenty — add a photo if
something looks different.</p>
<div class=card>
  <form id=f>
    <textarea name=message placeholder="He's finishing his food again and the ear looks less red…" required></textarea>
    <button class=primary type=submit>Send to my vet</button>
  </form>
  <div id=msg class=msg></div>
</div>
<script>
document.getElementById('f').addEventListener('submit', async function(e){
  e.preventDefault();
  const id = location.pathname.split('/').pop();
  const m = document.getElementById('msg');
  const body = {message: e.target.message.value};
  m.style.display='block'; m.className='msg'; m.textContent='Sending…';
  const r = await fetch('/api/care/followups/'+id,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j = await r.json();
  if(r.ok){ m.className='msg ok';
    m.innerHTML='<strong>Sent.</strong> Your vet will reply here — we\\'ll let you know.'; }
  else { m.className='msg bad'; m.textContent = j.error || 'Could not send'; }
});
</script>
""")


COMPOSER_HTML = _page("Write a care plan", """
<h1>Care plan</h1>
<p class=sub>Three things at the end of the appointment. The owner gets it with reminders
and, where you've picked a product, a way to buy it — credited to you.</p>
<div class=card>
  <form id=f>
    <label>Owner user ID</label><input name=owner_user_id required>
    <label>Pet ID</label><input name=pet_id>
    <label>State</label><input name=state value="NM" maxlength=2>
    <label>Summary</label><input name=summary placeholder="Recovery after ear infection">

    <h2>Medication</h2>
    <input name=med_title placeholder="Otic drops">
    <div style="display:flex;gap:10px">
      <input name=med_tpd placeholder="times/day e.g. 2" style="flex:1">
      <input name=med_days placeholder="days e.g. 7" style="flex:1">
    </div>

    <h2>Give / feed</h2>
    <input name=give_title placeholder="Joint supplement">
    <input name=give_product placeholder="product ID (optional)">

    <h2>Recheck</h2>
    <input name=recheck_on type=date>

    <button class=primary type=submit>Send plan to owner</button>
  </form>
  <div id=msg class=msg></div>
</div>
<script>
document.getElementById('f').addEventListener('submit', async function(e){
  e.preventDefault();
  const g = n => e.target[n] ? e.target[n].value.trim() : '';
  const items=[];
  if(g('med_title')) items.push({kind:'medication',title:g('med_title'),
     times_per_day:parseInt(g('med_tpd')||'0')||null, days:parseInt(g('med_days')||'0')||null});
  if(g('give_title')) items.push({kind:'give',title:g('give_title'),
     product_id: parseInt(g('give_product')||'0')||null});
  if(g('recheck_on')) items.push({kind:'recheck',title:'Recheck',due_on:g('recheck_on')});
  const m=document.getElementById('msg'); m.style.display='block'; m.className='msg';
  m.textContent='Sending…';
  const r = await fetch('/api/vet/plans',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({owner_user_id:parseInt(g('owner_user_id')), pet_id:parseInt(g('pet_id'))||null,
      state:g('state'), summary:g('summary'), items:items})});
  const j = await r.json();
  if(r.ok){ m.className='msg ok'; m.innerHTML='<strong>Plan sent.</strong> The owner sees it '+
    'now, with reminders for anything scheduled.'; e.target.reset(); }
  else { m.className='msg bad'; m.textContent=j.error||'Could not create the plan'; }
});
</script>
""")


def register_care_pages(app):
    @app.route("/care", methods=["GET"])
    def care_home_page():
        return CARE_HTML

    @app.route("/care/plan/<int:plan_id>", methods=["GET"])
    def care_plan_page(plan_id):
        return PLAN_HTML

    @app.route("/care/followup/<int:fid>", methods=["GET"])
    def care_followup_page(fid):
        return FOLLOWUP_HTML

    @app.route("/vet/plan/new", methods=["GET"])
    def vet_plan_composer():
        return COMPOSER_HTML
