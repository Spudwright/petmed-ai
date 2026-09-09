"""crittr.ai - deciding whether an own-label SKU is worth ordering, before ordering it.

WHY THIS EXISTS. The revenue share paid to partner practices is built, correct, and
currently worth nothing, because every product in the catalogue is an Amazon affiliate
out-link. A customer who clicks one leaves, no crittr order is created, attribution never
runs, and the practice earns $0 no matter what the rate says. product_economics.py surfaces
that for products we already have. This module is for the products we do not have yet.

An own-label SKU is the only thing that changes it, and it is the first decision in this
business that costs real money up front: a minimum order is cash spent before a single unit
sells. So the arithmetic should happen on a screen rather than in a meeting.

WHAT IT DELIBERATELY DOES NOT DO. It writes nothing. No product is created, no cost is
saved, no rate is changed. It is a calculator and a checklist - the same posture as
product_economics.py, which says of itself that it is "for deciding, not for retroactively
re-pricing anything that already happened". Committing a real product still goes through
the admin screens and, critically, through dropship.fulfillable(), which will not let a
thing be sold until it has a route to somebody's house.

THE THREE NUMBERS THAT ACTUALLY DECIDE IT, and why each is on the page:

  1. MARGIN AFTER THE VET'S SHARE. If the share exceeds the margin, the SKU loses money on
     exactly the sales the partnership is supposed to generate. That is not a rounding
     problem, it is an inverted incentive, and it is shown in red.
  2. CASH AT RISK. Minimum order quantity x unit cost, paid before revenue. A product can
     be beautifully profitable per unit and still be the wrong first order.
  3. WHAT A PRACTICE ACTUALLY RECEIVES PER MONTH. This is the number that gets quoted in a
     room to a veterinarian. Quoting it wrong is worse than not quoting it, because they
     will do the arithmetic themselves eventually.

THE CHECKLIST IS NOT LEGAL ADVICE and says so on the page. It is the set of things that
have to be true before a label can go on a jar, arranged so none of them is discovered late.

Public API
----------
    register_own_label_routes(app, admin_required)
"""
import os

_RATE_DEFAULT = os.environ.get(
    "CRITTR_REV_SHARE_PCT", os.environ.get("CRITTR_VET_REV_SHARE_PCT", "10")
)


# __RATE__ is substituted rather than formatted, because the page is mostly CSS and
# every brace in it would have to be doubled for str.format.
_PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>Own-label planner &middot; crittr</title>
<style>
body{margin:0;background:#FDFBF5;color:#1C2A1F;font:16px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:32px 20px 72px}
h1{font-size:28px;margin:0 0 6px}
h2{font-size:19px;margin:34px 0 12px}
p.sub{color:#6E7D70;margin:0 0 22px}
.card{background:#fff;border:1px solid #DFE5DB;border-radius:12px;padding:20px 22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}
label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#6E7D70;margin:0 0 5px}
input{width:100%;box-sizing:border-box;padding:9px 10px;border:1px solid #DFE5DB;border-radius:8px;font:inherit;background:#fff;color:#1C2A1F}
.out{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:20px}
.stat{background:#F6F8F4;border:1px solid #E4EADF;border-radius:10px;padding:13px 15px}
.stat .n{font-size:23px;font-weight:700;letter-spacing:-.01em}
.stat .l{font-size:12px;color:#6E7D70;margin-top:3px}
.stat.bad{background:#FBEDEA;border-color:#E7C3B9}
.stat.bad .n{color:#A32020}
.stat.warn{background:#FBF4E4;border-color:#E7DBB9}
.stat.warn .n{color:#7A5A17}
.flag{margin-top:16px;padding:13px 15px;border-radius:10px;font-size:14px;display:none}
.flag.show{display:block}
.flag.bad{background:#FBEDEA;border:1px solid #E7C3B9;color:#8A2C10}
.flag.warn{background:#FBF4E4;border:1px solid #E7DBB9;color:#6B4E12}
.flag.ok{background:#EAF5E9;border:1px solid #CBE3C7;color:#2D4A30}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #DFE5DB;border-radius:12px;margin-top:8px}
th{text-align:left;padding:8px 12px;color:#6E7D70;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
td{padding:10px 12px;border-top:1px solid #DFE5DB;vertical-align:top}
ul{margin:6px 0 0;padding-left:20px}li{margin:5px 0}
.tag{display:inline-block;font-size:11px;font-weight:700;padding:3px 8px;border-radius:99px}
.tag.a{background:#EAF5E9;color:#2D4A30}
.tag.b{background:#EAF0F5;color:#25445E}
.tag.no{background:#FBEDEA;color:#8A2C10}
.note{color:#6E7D70;font-size:13px;margin-top:10px}
</style></head><body><div class=wrap>

<h1>Own-label planner</h1>
<p class=sub>Every product in the catalogue today is an Amazon out-link, so a partner
practice earns <strong>$0</strong> on all of them. This works out whether a given own-label
SKU changes that, before any cash is committed. Nothing here is saved.</p>

<div class=card>
  <div class=grid>
    <div><label for=cost>Our cost / unit</label><input id=cost type=number step=0.01 value=9.50></div>
    <div><label for=price>Retail price</label><input id=price type=number step=0.01 value=29.99></div>
    <div><label for=moq>Minimum order (units)</label><input id=moq type=number step=1 value=1000></div>
    <div><label for=rate>Practice share %</label><input id=rate type=number step=0.5 value=__RATE__></div>
    <div><label for=units>Orders / practice / month</label><input id=units type=number step=1 value=40></div>
  </div>

  <div class=out id=out></div>
  <div class="flag" id=flag></div>
  <div class=note id=note></div>
  <div class=note><strong>The starting numbers are placeholders, not quotes.</strong> They are
  set deliberately conservative so the first thing you see is not a best case &mdash; a real
  contract-manufacturer quote is the only input worth trusting here.</div>
</div>

<h2>The starter set &mdash; four SKUs</h2>
<p class=sub style="margin-bottom:14px">Four daily consumables a veterinarian actually
recommends after a visit, each one a stock formula every contract manufacturer already
holds, covering dog and cat. Edit anything; totals update.</p>

<div class=card>
  <table id=settbl>
    <tr><th>Own-label SKU</th><th>Replaces</th><th style="width:110px">Retail</th>
        <th style="width:110px">Our cost</th><th style="width:150px">Margin &minus; share</th></tr>
  </table>

  <div class=grid style="margin-top:16px;max-width:520px">
    <div><label for=smoq>Minimum order / SKU</label><input id=smoq type=number step=1 value=500></div>
    <div><label for=srate>Practice share %</label><input id=srate type=number step=0.5 value=__RATE__></div>
    <div><label for=sunits>Orders / practice / month</label><input id=sunits type=number step=1 value=40></div>
  </div>

  <div class=out id=sout></div>
  <div class="flag" id=sflag></div>

  <div class=note><strong>Why these four and not the other five.</strong>
  Greenies and Oravet are extruded dental chews &mdash; different manufacturing entirely, and
  Oravet's coating is patented, so neither is a stock formula anyone will just print your name
  on. Pet-Tabs at $18.99 and VetriScience Nu Cat at $14.99 are too thin to carry a share once
  cost is real. Dasuquin is the premium version of the same joint category as Cosequin &mdash;
  one joint SKU first, the premium tier second if it sells.</div>
</div>

<h2>What has to be true before a label goes on a jar</h2>
<p class=sub style="margin-bottom:14px">Not legal advice. This is the sequence that stops
any of it being discovered late &mdash; have the finished label reviewed by someone who does
this for a living before the first production run.</p>

<table>
<tr><th style="width:132px">Track</th><th>Before you can sell it</th></tr>

<tr><td><span class="tag a">SUPPLEMENTS<br>&amp; TREATS</span><div class=note style="margin-top:6px">
Cosequin, Dasuquin, Composure, FortiFlora, Welactin, Pet-Tabs, VetriScience,
Greenies, Oravet &mdash; 9 of your 12</div></td>
<td><ul>
<li><strong>Contract manufacturer with a stock formula.</strong> You are buying an
equivalent formulation, not a copy of anyone's product. Ask for their existing joint /
calming chew before commissioning anything custom &mdash; lower minimum, faster, and the
formula is already proven.</li>
<li><strong>An AAFCO-style label panel:</strong> product name, guaranteed analysis,
full ingredient list, net quantity, directions, warnings, and <em>your</em> name and
address as the distributor. Most contract manufacturers will draft this; it is still
yours to be right.</li>
<li><strong>State feed registration</strong> for each state you ship to, with a per-product
fee. Your per-state compliance table already exists and starts with New Mexico &mdash; this
is the same shape of work.</li>
<li><strong>Claims discipline.</strong> A supplement that claims to treat or prevent a
disease is legally an unapproved drug. "Supports joint comfort" is a supplement;
"treats arthritis" is not. This is the single easiest way to turn a legal product into
an illegal one, and it happens in marketing copy, not on the label.</li>
<li><strong>NASC membership.</strong> Optional, and the one worth doing anyway: for a
vet-facing supplement brand it is the credibility marker a veterinarian looks for, and
it is the cheapest trust available to you.</li>
</ul></td></tr>

<tr><td><span class="tag b">FLEA &amp; TICK</span><div class=note style="margin-top:6px">
Frontline Gold, Seresto &mdash; EPA-registered pesticides</div></td>
<td><ul>
<li><strong>EPA supplemental distribution</strong> (40 CFR 152.132) is the mechanism that
does exactly what you asked for: the registrant authorises you to sell <em>their</em>
registered product under <em>your</em> brand name, same formulation, label otherwise
identical, carrying their EPA registration number with a distributor suffix.</li>
<li>The paperwork is not the hard part &mdash; <strong>the registrant's agreement is.</strong>
This is a commercial negotiation with Boehringer or Elanco, on their timetable, and it is
not where a first own-label SKU should start.</li>
<li>The alternative is a generic equivalent from a manufacturer who already holds their own
EPA registration for that active ingredient.</li>
</ul></td></tr>

<tr><td><span class="tag b">PHEROMONE</span><div class=note style="margin-top:6px">
Adaptil &mdash; the 12th, and in neither bucket</div></td>
<td><ul>
<li><strong>Check the category before assuming a track.</strong> A dog-appeasing pheromone
diffuser is not a feed and not obviously a pesticide, so neither the AAFCO route nor the EPA
route above is automatically the right one, and the device half (the plug-in) carries its own
requirements. It is one SKU &mdash; the point of listing it is that it does not silently
inherit whichever checklist it happens to sit next to.</li>
<li>Lowest-effort answer: leave it as an affiliate link and do the own-label work on the nine
supplements first.</li>
</ul></td></tr>

<tr><td><span class="tag no">NOT THIS</span></td>
<td><ul>
<li><strong>You cannot relabel a branded product and keep selling it as yours.</strong>
Taking Frontline Gold and putting CRITTR on it is trademark infringement, and because it is
an EPA-registered pesticide it is also a FIFRA violation. "Same label, different brand" is
what the regulator calls misbranding.</li>
<li><strong>Prescription products stay out.</strong> They need pharmacy licensure and
fulfilment you have deliberately chosen not to own &mdash; and it is why the four fake
CRITTR Rx generics were deleted for having no pharmacy behind them.</li>
</ul></td></tr>
</table>

<h2>Then, before it can go on sale</h2>
<div class=card>
<p style="margin:0">A product cannot be listed until it has a route to the customer.
<code>dropship.fulfillable()</code> enforces that, and
<code>enforce_stock_matches_fulfilment()</code> makes the shop obey it &mdash; so a signed
supplier and a fulfilment route are not paperwork to do afterwards, they are what makes the
"Add to cart" button legal to press. CRITTR Calm is the standing example: in stock,
buyable, and with nothing behind it.</p>
</div>

<script>
(function(){
  var $=function(i){return document.getElementById(i);};
  var money=function(c){return '$'+(c).toFixed(2);};
  function calc(){
    var cost=parseFloat($('cost').value)||0,
        price=parseFloat($('price').value)||0,
        moq=parseInt($('moq').value,10)||0,
        rate=parseFloat($('rate').value)||0,
        units=parseFloat($('units').value)||0;

    var margin=price-cost,
        share=price*(rate/100),
        net=margin-share,
        marginPct= price>0 ? (margin/price)*100 : 0,
        cash=moq*cost,
        recoup= net>0 ? Math.ceil(cash/net) : null,
        vetMonth=share*units,
        ourMonth=net*units;

    var cls=function(bad,warn){return bad?'stat bad':(warn?'stat warn':'stat');};
    $('out').innerHTML=
      '<div class="'+cls(margin<=0,marginPct<40)+'"><div class=n>'+money(margin)+'</div>'+
        '<div class=l>margin / unit ('+marginPct.toFixed(0)+'%)</div></div>'+
      '<div class="stat"><div class=n>'+money(share)+'</div><div class=l>practice share / unit</div></div>'+
      '<div class="'+cls(net<=0,false)+'"><div class=n>'+money(net)+'</div><div class=l>left to crittr / unit</div></div>'+
      '<div class="'+cls(false,cash>5000)+'"><div class=n>'+money(cash)+'</div><div class=l>cash for the minimum order</div></div>'+
      '<div class="stat"><div class=n>'+(recoup===null?'never':recoup.toLocaleString())+'</div>'+
        '<div class=l>units to recoup that cash</div></div>'+
      '<div class="stat"><div class=n>'+money(vetMonth)+'</div><div class=l>a practice earns / month</div></div>'+
      '<div class="stat"><div class=n>'+money(ourMonth)+'</div><div class=l>crittr earns / month, per practice</div></div>';

    var f=$('flag');
    f.className='flag show';
    if(margin<=0){
      f.classList.add('bad');
      f.innerHTML='<strong>This loses money on every unit</strong> before the practice takes anything. Cost is at or above retail.';
    } else if(net<=0){
      f.classList.add('bad');
      f.innerHTML='<strong>The '+rate+'% share exceeds the margin.</strong> Every sale through a partner practice loses money — the exact sales the partnership exists to create. Raise the price, cut the cost, or lower the rate.';
    } else if(marginPct<40){
      f.classList.add('warn');
      f.innerHTML='<strong>Thin for own-label.</strong> The point of making it yourself is a margin an affiliate link can never give you; under about 40% you are carrying inventory risk for very little.';
    } else {
      f.classList.add('ok');
      f.innerHTML='<strong>Works.</strong> A practice earns '+money(vetMonth)+' a month at '+units+
        ' orders, against '+money(0)+' on every affiliate product in the catalogue today. '+
        'You recoup the '+money(cash)+' order after '+recoup.toLocaleString()+' units.';
    }

    $('note').textContent='Today, on an affiliate product: the practice earns $0.00 and crittr earns $0.00, '+
      'because the customer leaves before an order exists.';
  }
  ['cost','price','moq','rate','units'].forEach(function(i){
    $(i).addEventListener('input',calc);
  });
  calc();

  /* ---- the starter set ---- */
  var SET=[
    ['CRITTR Joint (soft chew)','Cosequin DS Plus MSM',32.99,9.50],
    ['CRITTR Calm (soft chew)','Composure Pro',24.99,7.25],
    ['CRITTR Omega-3 (liquid)','Welactin Omega-3',28.99,8.00],
    ['CRITTR Biotic (sachets)','FortiFlora Probiotic',30.99,8.75]
  ];
  var tbl=$('settbl');
  SET.forEach(function(r,i){
    var tr=document.createElement('tr');
    tr.innerHTML='<td><strong>'+r[0]+'</strong></td><td class=note style="margin:0">'+r[1]+'</td>'+
      '<td><input class=sp data-i="'+i+'" type=number step=0.01 value="'+r[2].toFixed(2)+'"></td>'+
      '<td><input class=sc data-i="'+i+'" type=number step=0.01 value="'+r[3].toFixed(2)+'"></td>'+
      '<td id="sm'+i+'"></td>';
    tbl.appendChild(tr);
  });

  function setCalc(){
    var moq=parseInt($('smoq').value,10)||0,
        rate=parseFloat($('srate').value)||0,
        units=parseFloat($('sunits').value)||0,
        cash=0,netSum=0,shareSum=0,anyBad=false;

    SET.forEach(function(_,i){
      var price=parseFloat(document.querySelector('.sp[data-i="'+i+'"]').value)||0,
          cost=parseFloat(document.querySelector('.sc[data-i="'+i+'"]').value)||0,
          m=price-cost, sh=price*(rate/100), net=m-sh;
      if(net<=0) anyBad=true;
      cash+=cost*moq; netSum+=net; shareSum+=sh;
      $('sm'+i).innerHTML = net<=0
        ? '<span style="color:#A32020;font-weight:700">'+money(m)+' &minus; '+money(sh)+' = '+money(net)+'</span>'
        : money(m)+' &minus; '+money(sh)+' = <strong>'+money(net)+'</strong>';
    });

    var avgNet=netSum/SET.length, avgShare=shareSum/SET.length,
        breakeven= avgNet>0 ? Math.ceil(cash/avgNet) : null,
        practiceMonths= (avgNet>0 && units>0) ? Math.ceil(breakeven/units) : null;

    $('sout').innerHTML=
      '<div class="stat'+(cash>25000?' warn':'')+'"><div class=n>'+money(cash)+'</div>'+
        '<div class=l>cash up front, all 4 SKUs</div></div>'+
      '<div class=stat><div class=n>'+(breakeven===null?'never':breakeven.toLocaleString())+'</div>'+
        '<div class=l>units to break even</div></div>'+
      '<div class=stat><div class=n>'+(practiceMonths===null?'—':practiceMonths.toLocaleString())+'</div>'+
        '<div class=l>practice-months to get there</div></div>'+
      '<div class=stat><div class=n>'+money(avgShare*units)+'</div>'+
        '<div class=l>a practice earns / month</div></div>'+
      '<div class=stat><div class=n>'+money(avgNet*units)+'</div>'+
        '<div class=l>crittr / month, per practice</div></div>';

    var f=$('sflag'); f.className='flag show';
    if(anyBad){
      f.classList.add('bad');
      f.innerHTML='<strong>At least one SKU loses money once the practice takes its share.</strong> Fix it before ordering four of anything.';
    } else if(cash>25000){
      f.classList.add('warn');
      f.innerHTML='<strong>'+money(cash)+' is a lot to commit before a single own-label unit has sold.</strong> '+
        'Most contract manufacturers will do 500 on a stock formula, and some will go lower on a first order — '+
        'halving the minimum halves the risk and costs you only a slightly worse unit price. '+
        'Ask for the 500 and the 1,000 quote side by side.';
    } else {
      f.classList.add('ok');
      f.innerHTML='<strong>'+money(cash)+' at risk, back after '+breakeven.toLocaleString()+' units.</strong> '+
        'That is roughly '+practiceMonths+' practice-months at '+units+' orders each — so '+
        Math.ceil(practiceMonths/12)+' practices for a year, or '+Math.ceil(practiceMonths/6)+' for six months. '+
        'Judge the order by how many clinics you can actually sign, not by the margin.';
    }
  }
  ['smoq','srate','sunits'].forEach(function(i){ $(i).addEventListener('input',setCalc); });
  document.querySelectorAll('.sp,.sc').forEach(function(el){ el.addEventListener('input',setCalc); });
  setCalc();
})();
</script>
</div></body></html>"""


def register_own_label_routes(app, admin_required):
    """Wire GET /admin/own-label. Read-only: it writes nothing and reads nothing."""

    @app.route("/admin/own-label", methods=["GET"])
    @admin_required
    def admin_own_label_page():
        return _PAGE.replace("__RATE__", str(_RATE_DEFAULT))
