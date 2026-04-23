"""crittr.ai — shared MEDVi-style header/drawer/promo bundle.

Exposes three strings that any template can concatenate in:
    SHARED_NAV_CSS, SHARED_NAV_HTML, SHARED_NAV_JS

Templates are plain strings, NOT Jinja — they use {active_slug} Python format
slots, so callers `.format(active_slug="dogs")` if they want to highlight a
drawer item.  Pass "" when no slug applies.
"""

SHARED_NAV_CSS = r"""
/* === shared MEDVi-style header (promo + nav + drawer) === */
:root {
  --s-sage-50:#F2F7F1;--s-sage-100:#E4EFE2;--s-sage-200:#C7DEC4;--s-sage-300:#A6C9A2;
  --s-sage-400:#82B17D;--s-sage-500:#6B9E6B;--s-sage-600:#527E54;--s-sage-700:#3E6340;
  --s-sage-800:#2D4A30;--s-sage-900:#1F3221;
  --s-cream:#FDFBF5;--s-cream-2:#F6F1E7;
  --s-ink:#1C2A1F;--s-muted:#6E7D70;--s-line:#DFE5DB;
  --s-radius:14px;--s-radius-lg:22px;--s-ease:cubic-bezier(.32,.72,.24,1);
}
.s-announce{background:var(--s-sage-800);color:#EEF3EC;font-size:.82rem;text-align:center;padding:.55rem 1rem;letter-spacing:.02em;position:relative}
.s-announce strong{color:#fff;font-weight:600}
.s-announce .s-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#6ECB70;margin:0 .5rem -1px;box-shadow:0 0 0 3px rgba(110,203,112,.25)}
.s-announce-x{position:absolute;right:.9rem;top:50%;transform:translateY(-50%);background:transparent;border:0;color:#D2E3CF;font-size:1.4rem;line-height:1;cursor:pointer;padding:0 .35rem;opacity:.7}
.s-announce-x:hover{opacity:1;color:#fff}
.s-announce.is-hidden{display:none}

.s-nav{position:sticky;top:0;z-index:40;background:rgba(253,251,245,.92);backdrop-filter:saturate(1.6) blur(10px);-webkit-backdrop-filter:saturate(1.6) blur(10px);border-bottom:1px solid rgba(223,229,219,.6)}
.s-nav-inner{display:flex;align-items:center;justify-content:space-between;height:72px;max-width:1220px;margin:0 auto;padding:0 28px}
.s-logo{display:flex;align-items:center;gap:.55rem;font-family:'Fraunces',serif;font-size:1.88rem;font-weight:700;color:var(--s-sage-800);letter-spacing:-.028em;text-decoration:none}
.s-logo-dot{width:16px;height:16px;border-radius:50%;background:var(--s-sage-500);box-shadow:0 0 0 4px var(--s-sage-100);display:inline-block}
.s-nav-right{display:flex;align-items:center;gap:.6rem}

.s-cart-btn{position:relative;padding:.55rem;border-radius:50%;border:1px solid var(--s-line);background:#fff;cursor:pointer;margin-right:.4rem}
.s-cart-btn:hover{background:var(--s-sage-50);border-color:var(--s-sage-300)}
.s-cart-count{position:absolute;top:-4px;right:-4px;min-width:18px;height:18px;padding:0 5px;border-radius:9px;background:var(--s-sage-700);color:#fff;font-size:.7rem;font-weight:700;display:flex;align-items:center;justify-content:center}
.s-cart-count.hidden{display:none}

.s-btn-pill{display:inline-flex;align-items:center;justify-content:center;padding:.7rem 1.25rem;font-size:.9rem;border-radius:999px;font-weight:600;background:var(--s-ink);color:#fff;border:1px solid var(--s-ink);cursor:pointer;text-decoration:none;transition:all .2s var(--s-ease)}
.s-btn-pill:hover{background:var(--s-sage-900);border-color:var(--s-sage-900)}

.s-burger{display:inline-flex;flex-direction:column;justify-content:center;align-items:center;gap:5px;width:42px;height:42px;padding:0;border-radius:50%;border:1px solid var(--s-line);background:#fff;cursor:pointer;margin-left:.35rem}
.s-burger:hover{background:var(--s-sage-50);border-color:var(--s-sage-300)}
.s-burger span{display:block;width:18px;height:2px;background:var(--s-ink);border-radius:2px}

@media(max-width:520px){.s-btn-pill{padding:.6rem 1rem;font-size:.82rem}.s-cart-btn{padding:.5rem}}

.s-drawer-overlay{position:fixed;inset:0;background:rgba(28,42,31,.45);opacity:0;pointer-events:none;transition:opacity .25s var(--s-ease);z-index:80}
.s-drawer-overlay.is-open{opacity:1;pointer-events:auto}
.s-drawer{position:fixed;top:0;right:0;bottom:0;width:min(420px,92vw);background:var(--s-cream);transform:translateX(100%);transition:transform .32s var(--s-ease);z-index:90;display:flex;flex-direction:column;box-shadow:-24px 0 60px -24px rgba(28,42,31,.32)}
.s-drawer.is-open{transform:translateX(0)}
.s-drawer-head{display:flex;align-items:center;justify-content:space-between;padding:1.1rem 1.25rem;border-bottom:1px solid var(--s-line)}
.s-drawer-head .s-logo{font-size:1.78rem;font-weight:700}
.s-drawer-close{background:transparent;border:0;font-size:1.6rem;line-height:1;padding:.25rem .55rem;cursor:pointer;color:var(--s-ink);border-radius:8px}
.s-drawer-close:hover{background:var(--s-sage-50)}
.s-drawer-body{flex:1;overflow-y:auto;padding:.6rem 0 1rem}
.s-drawer-link{display:flex;align-items:center;justify-content:space-between;padding:1rem 1.25rem;color:var(--s-ink);font-size:1rem;font-weight:500;border-bottom:1px solid rgba(223,229,219,.55);text-decoration:none}
.s-drawer-link:hover{background:var(--s-sage-50);color:var(--s-sage-700)}
.s-drawer-link.is-active{background:var(--s-sage-50);color:var(--s-sage-800);font-weight:600}
.s-drawer-foot{padding:1rem 1.25rem 1.3rem;border-top:1px solid var(--s-line);background:var(--s-cream-2)}
.s-drawer-foot .s-btn-pill{width:100%;padding:1rem 1.5rem;font-size:1rem}
.s-drawer-foot-mini{display:flex;justify-content:space-between;gap:.5rem;padding:.65rem 0 .85rem;font-size:.86rem}
.s-drawer-foot-mini a{color:var(--s-muted);font-weight:500;text-decoration:none}
.s-drawer-foot-mini a:hover{color:var(--s-sage-700)}

body.drawer-lock{overflow:hidden}
"""

SHARED_NAV_HTML = """
<!-- shared promo banner -->
<div class="s-announce" id="s-promoBanner">
  <span class="s-dot"></span>
  <strong>Free 24/7 AI triage</strong> — ER, vet tomorrow, or safe at home, answered in 60 seconds.
  <button class="s-announce-x" onclick="sDismissPromo()" aria-label="Dismiss">\u00d7</button>
</div>

<!-- shared nav -->
<nav class="s-nav">
  <div class="s-nav-inner">
    <a href="/" class="s-logo"><span class="s-logo-dot"></span>crittr</a>
    <div class="s-nav-right">
      <button class="s-cart-btn" onclick="if(typeof openCart==='function')openCart()" aria-label="Cart">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13l-2.3-8M7 13l-1.5 3h11M10 19a1 1 0 1 1-2 0 1 1 0 0 1 2 0zM18 19a1 1 0 1 1-2 0 1 1 0 0 1 2 0z"/></svg>
        <span class="s-cart-count hidden" id="cartCount">0</span>
      </button>
      <a href="/#hero-chat" class="s-btn-pill">Start triage</a>
      <button class="s-burger" onclick="sOpenDrawer()" aria-label="Open menu">
        <span></span><span></span><span></span>
      </button>
    </div>
  </div>
</nav>

<!-- shared drawer -->
<div class="s-drawer-overlay" id="s-drawerOverlay" onclick="sCloseDrawer()"></div>
<aside class="s-drawer" id="s-drawer" role="dialog" aria-label="Menu" aria-hidden="true">
  <div class="s-drawer-head">
    <a href="/" class="s-logo"><span class="s-logo-dot"></span>crittr</a>
    <button class="s-drawer-close" onclick="sCloseDrawer()" aria-label="Close menu">\u00d7</button>
  </div>
  <div class="s-drawer-body">
    <a class="s-drawer-link" href="/#hero-chat">Start triage <span>\u203a</span></a>
    <a class="s-drawer-link" href="/#how">How it works <span>\u203a</span></a>
    <a class="s-drawer-link __DOGS_ACTIVE__" href="/shop/dogs">Dogs <span>\u203a</span></a>
    <a class="s-drawer-link __CATS_ACTIVE__" href="/shop/cats">Cats <span>\u203a</span></a>
    <a class="s-drawer-link __SUPP_ACTIVE__" href="/shop/supplements">Supplements <span>\u203a</span></a>
    <a class="s-drawer-link __RX_ACTIVE__" href="/shop/rx">Prescriptions <span>\u203a</span></a>
    <a class="s-drawer-link" href="/find-vet">Find a vet <span>\u203a</span></a>
    <a class="s-drawer-link" href="/refer">Refer a friend <span>\u203a</span></a>
    <a class="s-drawer-link" href="/#faq">FAQ <span>\u203a</span></a>
  </div>
  <div class="s-drawer-foot">
    <div class="s-drawer-foot-mini">
      <a href="#" onclick="sCloseDrawer();if(typeof openAuth==='function')openAuth();return false;">Sign in</a>
      <a href="/#faq" onclick="sCloseDrawer()">FAQ</a>
    </div>
    <a class="s-btn-pill" href="/#hero-chat" onclick="sCloseDrawer()">Start triage</a>
  </div>
</aside>
"""

SHARED_NAV_JS = r"""
<script>
(function(){
  function byId(id){return document.getElementById(id);}
  window.sOpenDrawer = function(){
    var d=byId('s-drawer'), o=byId('s-drawerOverlay');
    if(!d) return;
    d.classList.add('is-open'); o.classList.add('is-open');
    d.setAttribute('aria-hidden','false');
    document.body.classList.add('drawer-lock');
  };
  window.sCloseDrawer = function(){
    var d=byId('s-drawer'), o=byId('s-drawerOverlay');
    if(!d) return;
    d.classList.remove('is-open'); o.classList.remove('is-open');
    d.setAttribute('aria-hidden','true');
    document.body.classList.remove('drawer-lock');
  };
  document.addEventListener('keydown', function(e){ if(e.key==='Escape') window.sCloseDrawer(); });
  window.sDismissPromo = function(){
    var b=byId('s-promoBanner'); if(b) b.classList.add('is-hidden');
    try { localStorage.setItem('crittr_promo_dismissed', String(Date.now())); } catch(e){}
  };
  try {
    var prev = Number(localStorage.getItem('crittr_promo_dismissed')||0);
    if (prev && (Date.now() - prev) < 7*86400*1000) {
      var b=byId('s-promoBanner'); if(b) b.classList.add('is-hidden');
    }
  } catch(e){}
})();
</script>
"""


def render_nav_html(active_slug: str = "") -> str:
    """Return SHARED_NAV_HTML with drawer link highlighted for *active_slug*.

    active_slug is one of 'dogs', 'cats', 'supplements', 'rx', or '' (none).
    """
    map_ = {
        "dogs":        ("__DOGS_ACTIVE__", "is-active"),
        "cats":        ("__CATS_ACTIVE__", "is-active"),
        "supplements": ("__SUPP_ACTIVE__", "is-active"),
        "rx":          ("__RX_ACTIVE__",   "is-active"),
    }
    html = SHARED_NAV_HTML
    for k, (placeholder, cls) in map_.items():
        html = html.replace(placeholder, cls if k == active_slug else "")
    # Clear any remaining placeholders
    for placeholder, _ in map_.values():
        html = html.replace(placeholder, "")
    return html
