"""crittr.ai — MEDVi-style category shop pages (Phase A.2).

Renders `/shop/<category>` pages that look like MEDVi's category tiles:
  * Short clinical-feeling headline for the category
  * 5-10 curated vet-picked SKUs
  * Price + Add-to-cart OR "Start consult" gate for Rx items
  * Sidebar link to "Not sure? Start with triage"

Categories
----------
    /shop/dogs          -> all products tagged species contains "dog"
    /shop/cats          -> all products tagged species contains "cat"
    /shop/supplements   -> category slug 'supplements' / 'wellness'
    /shop/rx            -> products where requires_rx = TRUE

Public API
----------
    register_shop_routes(app, q)
"""
import logging
from flask import render_template_string, abort, Response, url_for
from shared_nav import SHARED_NAV_CSS, SHARED_NAV_JS, render_nav_html

log = logging.getLogger("crittr.shop")


def _tag_match(tags, needles):
    """Return True if any of *needles* appears in *tags*.

    Tolerant of tags being a list[str], a comma-separated string, or None.
    """
    if not tags:
        return False
    if isinstance(tags, (list, tuple, set)):
        hay = " ".join(str(t).lower() for t in tags)
    else:
        hay = str(tags).lower()
    return any(n.lower() in hay for n in needles)


_CATEGORIES = {
    "dogs": {
        "title": "For dogs",
        "hero_eyebrow": "Curated for dogs",
        "hero_lede": (
            "Everything here was picked by our vet advisors for dogs specifically. "
            "Not sure where to start? Describe what's going on — our triage chat "
            "will surface what your dog actually needs."
        ),
        "filter": lambda p: "dog" in (p.get("species") or "").lower(),
    },
    "cats": {
        "title": "For cats",
        "hero_eyebrow": "Curated for cats",
        "hero_lede": (
            "Cats aren't small dogs. Everything here was chosen by our vet advisors "
            "for feline physiology. If your cat is acting off, start with a triage "
            "chat — we'll help you read the signs."
        ),
        "filter": lambda p: "cat" in (p.get("species") or "").lower(),
    },
    "supplements": {
        "title": "Supplements & daily wellness",
        "hero_eyebrow": "Daily wellness",
        "hero_lede": (
            "Joint, skin, gut, calm — the supplements our vet advisors actually "
            "recommend, and the dosing they actually use. Not a wall of SKUs."
        ),
        "filter": lambda p: (
            _tag_match(p.get("tags"), ("supplement", "supplements", "wellness"))
            or (p.get("category_slug") or "") in ("supplements", "wellness")
        ),
    },
    "rx": {
        "title": "Prescriptions",
        "hero_eyebrow": "Prescription",
        "hero_lede": (
            "Prescription items always start with a consult. Our licensed pharmacy "
            "partner (Chewy Pharmacy) handles fulfillment — we handle the triage. "
            "Tap any item to start a consult; if it's the right fit, the vet writes "
            "the Rx and we ship."
        ),
        "filter": lambda p: bool(p.get("requires_rx")),
    },
}


_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:type" content="website">
<meta property="og:title" content="{{ cat.title }} — crittr.ai">
<meta property="og:description" content="{{ cat.hero_lede }}">
<meta property="og:url" content="https://crittr.ai/shop/{{ slug }}">
<meta property="og:image" content="https://crittr.ai/og/shop-{{ slug }}.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{{ cat.title }} — crittr.ai">
<meta name="twitter:description" content="{{ cat.hero_lede }}">
<meta name="twitter:image" content="https://crittr.ai/og/shop-{{ slug }}.png">
<title>{{ cat.title }} — crittr.ai</title>
<meta name="description" content="{{ cat.hero_lede[:160] }}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--sage-50:#F2F7F1;--sage-100:#E4EFE2;--sage-200:#C7DEC4;--sage-300:#A6C9A2;--sage-400:#82B17D;--sage-500:#6B9E6B;--sage-600:#527E54;--sage-700:#3E6340;--sage-800:#2D4A30;--sage-900:#1F3221;--cream:#FDFBF5;--cream-2:#F6F1E7;--ink:#1C2A1F;--muted:#6E7D70;--line:#DFE5DB;--terracotta:#D4956A;--danger:#B4463B;--radius:14px;--radius-sm:10px;--radius-lg:22px;--ease:cubic-bezier(.32,.72,.24,1);--shadow:0 12px 40px -16px rgba(28,42,31,.18)}
*{box-sizing:border-box}
body{margin:0;font-family:'Inter',system-ui,sans-serif;color:var(--ink);background:var(--cream);line-height:1.55}
h1,h2,h3,h4{font-family:'Fraunces',serif;font-weight:500;color:var(--sage-900);margin:0 0 .4em;letter-spacing:-.01em;line-height:1.15}
a{color:var(--sage-700);text-decoration:none}
.muted{color:var(--muted)}
.container{max-width:1140px;margin:0 auto;padding:0 1.5rem}
nav{padding:1.1rem 0;border-bottom:1px solid var(--line);background:var(--cream)}
nav .row{display:flex;align-items:center;justify-content:space-between;gap:1rem}
nav .logo{font-family:'Fraunces',serif;font-size:1.45rem;font-weight:600;color:var(--sage-800);display:flex;align-items:center;gap:.55rem}
nav .logo .dot{width:10px;height:10px;border-radius:50%;background:var(--sage-500);box-shadow:0 0 0 4px rgba(167,202,161,.3)}
nav ul{display:flex;gap:1.4rem;list-style:none;margin:0;padding:0;flex-wrap:wrap}
nav ul li a{color:var(--ink);font-size:.92rem;font-weight:500}
nav ul li a.active{color:var(--sage-700);border-bottom:2px solid var(--sage-600);padding-bottom:2px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:.45rem;padding:.75rem 1.35rem;border-radius:999px;border:1px solid transparent;font-weight:600;font-size:.92rem;cursor:pointer;font-family:inherit;transition:all .2s var(--ease)}
.btn-primary{background:var(--sage-700);color:#fff}
.btn-primary:hover{background:var(--sage-800)}
.btn-secondary{background:#fff;color:var(--sage-800);border-color:var(--line)}
.btn-secondary:hover{background:var(--sage-50)}
.btn-sm{padding:.55rem 1rem;font-size:.85rem}
.cart-btn{position:relative;padding:.5rem;border-radius:50%;border:1px solid var(--line);background:#fff;cursor:pointer}
.cart-count{position:absolute;top:-4px;right:-4px;background:var(--terracotta);color:#fff;font-size:.65rem;font-weight:700;width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;border:2px solid var(--cream)}
.cart-count.hidden{display:none}
.hero{padding:3.5rem 0 2rem}
.hero .eyebrow{font-size:.75rem;letter-spacing:.16em;text-transform:uppercase;color:var(--sage-600);font-weight:700;margin-bottom:.8rem}
.hero h1{font-size:2.6rem;max-width:720px;margin:0 0 1rem}
.hero p{max-width:640px;color:var(--ink);font-size:1.08rem}
.shop-grid{display:grid;grid-template-columns:1fr 280px;gap:2.5rem;padding:2rem 0 5rem}
@media(max-width:880px){.shop-grid{grid-template-columns:1fr}}
.products{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:1.25rem}
.product-card{background:#fff;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;display:flex;flex-direction:column;transition:box-shadow .2s var(--ease),transform .2s var(--ease)}
.product-card:hover{box-shadow:var(--shadow);transform:translateY(-2px)}
.product-img{aspect-ratio:1/1;background:var(--sage-50);background-size:cover;background-position:center;position:relative}
.product-badge{position:absolute;top:.7rem;left:.7rem;background:var(--sage-800);color:#fff;font-size:.7rem;font-weight:700;padding:.25rem .6rem;border-radius:999px;letter-spacing:.05em;text-transform:uppercase}
.product-badge.rx{background:var(--terracotta)}
.product-body{padding:1.1rem;display:flex;flex-direction:column;flex:1}
.product-body h3{font-size:1.05rem;margin:0 0 .2rem}
.product-species{font-size:.78rem;color:var(--muted);text-transform:capitalize;margin-bottom:.8rem}
.product-price{font-family:'Fraunces',serif;font-size:1.25rem;font-weight:600;color:var(--sage-900);margin:.4rem 0 1rem}
.product-price .compare{color:var(--muted);font-size:.9rem;font-weight:400;text-decoration:line-through;margin-left:.4rem}
.product-actions{margin-top:auto;display:flex;gap:.5rem}
.product-actions .btn{flex:1}
.sidebar{background:linear-gradient(160deg,var(--sage-50),#fff);border:1px solid var(--line);border-radius:var(--radius);padding:1.75rem;position:sticky;top:1.25rem;height:fit-content}
.sidebar h3{font-size:1.2rem;margin:0 0 .6rem}
.sidebar p{font-size:.92rem;color:var(--ink);margin:0 0 1rem}
.empty{background:#fff;border:1px dashed var(--line);border-radius:var(--radius);padding:3rem 1.5rem;text-align:center;color:var(--muted);grid-column:1/-1}
footer{background:var(--sage-900);color:#EEF3EC;padding:2rem 0;text-align:center;font-size:.85rem}
footer a{color:#B2C3B2}
.ch-row{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:1rem}
.ch-row a{font-size:.82rem;background:#fff;border:1px solid var(--line);padding:.35rem .75rem;border-radius:999px;color:var(--sage-800)}
.ch-row a.active{background:var(--sage-700);color:#fff;border-color:var(--sage-700)}
{{ shared_nav_css|safe }}
</style>
</head>
<body>
{{ shared_nav_html|safe }}
<section class="hero">
  <div class="container">
    <div class="eyebrow">{{ cat.hero_eyebrow }}</div>
    <h1>{{ cat.title }}</h1>
    <p>{{ cat.hero_lede }}</p>
    <div class="ch-row">
      <a href="/shop/dogs" {% if slug=='dogs' %}class="active"{% endif %}>Dogs</a>
      <a href="/shop/cats" {% if slug=='cats' %}class="active"{% endif %}>Cats</a>
      <a href="/shop/supplements" {% if slug=='supplements' %}class="active"{% endif %}>Supplements</a>
      <a href="/shop/rx" {% if slug=='rx' %}class="active"{% endif %}>Prescriptions</a>
    </div>
  </div>
</section>
<div class="container">
  <div class="shop-grid">
    <div class="products">
      {% for p in products %}
        <div class="product-card">
          <div class="product-img" style="background-image:url('{{ p.image_url or '/static/og/default.png' }}')">
            {% if p.requires_rx %}<span class="product-badge rx">Rx</span>{% endif %}
            {% if p.compare_price_cents and p.price_cents < p.compare_price_cents %}<span class="product-badge">Sale</span>{% endif %}
          </div>
          <div class="product-body">
            <h3>{{ p.name }}</h3>
            <div class="product-species">{{ p.species or 'All pets' }}</div>
            <div class="product-price">${{ '{:.2f}'.format(p.price_cents/100) }}{% if p.compare_price_cents and p.price_cents < p.compare_price_cents %}<span class="compare">${{ '{:.2f}'.format(p.compare_price_cents/100) }}</span>{% endif %}</div>
            <div class="product-actions">
              {% if p.requires_rx %}
                <button class="btn btn-secondary" onclick='addOrConsult({{ p|tojson }})'>Start consult</button>
              {% elif p.amazon_url %}
                <a class="btn btn-primary" href="{{ p.amazon_url }}" target="_blank" rel="nofollow noopener sponsored">Buy on Amazon</a>
              {% elif p.chewy_url %}
                <a class="btn btn-primary" href="{{ p.chewy_url }}" target="_blank" rel="nofollow noopener sponsored">Buy on Chewy</a>
              {% else %}
                <button class="btn btn-primary" onclick='addOrConsult({{ p|tojson }})'>Add to cart</button>
              {% endif %}
            </div>
          </div>
        </div>
      {% else %}
        <div class="empty">Nothing here yet. Our vet advisors are still curating this category — or start with a triage chat to get a personalized pick.</div>
      {% endfor %}
    </div>
    <aside class="sidebar">
      <h3>Not sure?</h3>
      <p>Every product here was picked by our vet advisors, but the fastest way to know what your critter actually needs is to describe what's going on.</p>
      <a href="/#hero-chat" class="btn btn-primary" style="width:100%">Start a free triage</a>
    </aside>
  </div>
</div>
<footer>
  <div class="container">
    crittr.ai · Licensed veterinary pharmacy · USA · <a href="mailto:hello@crittr.ai">hello@crittr.ai</a>
  </div>
</footer>
<!-- Load shared cart + auth SPA from the homepage (it's already there as /static/shop-cart.js) -->
<script>
// Minimal cart boot: pull shared helpers out of index-v2.html by copy.
// To keep this page self-contained we include the same cart + auth JS inline.
const $=(s,el=document)=>el.querySelector(s);
const State={cart:JSON.parse(localStorage.getItem('crittr_cart')||'[]'),products:{{ products|tojson }},user:null,authMode:'login'};
const fmt=(cents)=>`$${(cents/100).toFixed(2)}`;
const api=async(url,opts={})=>{const r=await fetch(url,{credentials:'include',headers:{'Content-Type':'application/json'},...opts});const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.error||`HTTP ${r.status}`);return data;};
const saveCart=()=>localStorage.setItem('crittr_cart',JSON.stringify(State.cart));
function updateCartCount(){const n=State.cart.reduce((s,c)=>s+c.quantity,0);const el=$('#cartCount');if(!el)return;el.textContent=n;el.classList.toggle('hidden',n===0);}
function toast(msg){alert(msg);}
function addToCart(product){if(!product||!product.id)return;const existing=State.cart.find(c=>c.product_id===product.id);if(existing)existing.quantity++;else State.cart.push({product_id:product.id,quantity:1,name:product.name,price_cents:product.price_cents,image_url:product.image_url,species:product.species});saveCart();updateCartCount();toast(`Added ${product.name} to cart`);}
function addOrConsult(product){
  if (product && product.requires_rx){
    try {
      sessionStorage.setItem('crittr_pending_rx', JSON.stringify({
        id: product.id, slug: product.slug, name: product.name,
        price_cents: product.price_cents, requires_rx: true
      }));
    } catch(e){}
    // Send the user to the homepage triage chat with pending Rx seed in storage.
    location.href = '/#hero-chat';
    return;
  }
  addToCart(product);
}
function openCart(){location.href='/#cart';/* homepage has the real drawer */}
function openAuth(){location.href='/login';}
document.addEventListener('DOMContentLoaded',updateCartCount);
</script>
{{ shared_nav_js|safe }}
</body>
</html>
"""


def register_shop_routes(app, q):
    """Wire GET /shop/<slug>."""
    @app.route("/shop/<slug>")
    def shop_page(slug):
        cat = _CATEGORIES.get(slug)
        if not cat:
            abort(404)
        try:
            rows = q("""
                SELECT p.id, p.name, p.slug, p.description, p.price_cents,
                       p.compare_price_cents, p.image_url, p.species,
                       p.requires_rx, p.tags, p.amazon_url, p.chewy_url,
                       c.slug AS category_slug
                  FROM products p
             LEFT JOIN categories c ON c.id = p.category_id
                 WHERE p.in_stock = TRUE
              ORDER BY p.id
            """) or []
        except Exception as e:
            log.warning(f"shop query failed: {e}")
            rows = []
        products = [p for p in rows if cat["filter"](p)]
        return render_template_string(
            _HTML,
            cat=cat, slug=slug, products=products,
            shared_nav_css=SHARED_NAV_CSS,
            shared_nav_html=render_nav_html(slug),
            shared_nav_js=SHARED_NAV_JS,
        )
