"""crittr.ai - the account page.

Why this file exists
--------------------
`/account` was linked from three places and existed in none. Flask's catch-all
answered it with the marketing homepage and an HTTP 200, so every one of these
dead-ended silently - no error, no clue, just the sales page again:

    1. the nav button, which becomes the signed-in customer's own name
    2. "View orders" on the order-success screen, i.e. the click a customer
       makes in the ten seconds AFTER paying
    3. `POST /api/portal`, whose Stripe `return_url` is literally
       `{app_url}/account` - so Stripe hands a paying subscriber back to us and
       we show them the homepage

(3) is the one that stings: the route was already load-bearing inside a paid
flow before anything served it.

What it shows
-------------
Everything is fetched client-side from APIs that already existed, so this page
adds no new data access and no new schema:

    GET  /api/me            who you are
    GET  /api/orders        order history (login_required)
    GET  /api/subscriptions active plans (login_required, from stripe_routes)
    GET  /api/referrals/me  your share link and credit balance
    POST /api/portal        Stripe billing portal hand-off
    POST /api/logout        sign out

Signed-out visitors get a sign-in form here rather than a redirect home. The
auth modal lives inside the SPA, so bouncing them to `/` to go find it is how
somebody arriving from a Stripe return_url or a bookmark loses the thread.

Public API
----------
    register_account_routes(app)
"""
from flask import render_template_string
from shared_nav import SHARED_NAV_CSS, SHARED_NAV_JS, render_nav_html


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Your account - crittr.ai</title>
<meta name="description" content="Your crittr orders, subscriptions and referral credit.">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- An account page has nothing a search engine should hold on to, and
     everything here sits behind a session. -->
<meta name="robots" content="noindex,nofollow">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
body{margin:0;font-family:Inter,system-ui,sans-serif;background:#FDFBF5;color:#1C2A1F;line-height:1.6;-webkit-font-smoothing:antialiased}
h1,h2,h3{font-family:'Fraunces',serif;font-weight:600;color:#1F3221;line-height:1.2}
h1{font-size:clamp(2rem,4vw,2.7rem);margin:0 0 .4rem}
h2{font-size:1.35rem;margin:0 0 1rem}
p{margin:0 0 1em;color:#3D4F40}
a{color:#3E6340}
.wrap{max-width:820px;margin:0 auto;padding:56px 24px 100px}
.eyebrow{display:inline-block;font-size:.76rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:#527E54;margin-bottom:.9rem}
.sub{color:#6E7D70;margin:0 0 2.2rem;padding-bottom:1.3rem;border-bottom:1px solid #DFE5DB}
.card{background:#fff;border:1px solid #DFE5DB;border-radius:14px;padding:22px 24px;margin:0 0 18px}
.card h2{display:flex;align-items:center;justify-content:space-between;gap:1rem}
.muted{color:#6E7D70;font-size:.94rem}
.btn{display:inline-block;border:0;border-radius:999px;padding:.7rem 1.35rem;font:inherit;font-weight:600;font-size:.95rem;cursor:pointer;text-decoration:none;background:#3E6340;color:#fff}
.btn:hover{background:#33532F}
.btn.ghost{background:transparent;color:#3E6340;border:1px solid #C6D2C2}
.btn.ghost:hover{background:#F1F5EF}
.btn[disabled]{opacity:.55;cursor:default}
.row{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center}
.order{border-top:1px solid #EDF1EB;padding:14px 0;display:flex;flex-wrap:wrap;gap:.4rem 1rem;align-items:baseline;justify-content:space-between}
.order:first-of-type{border-top:0}
.order .items{flex:1 1 60%;min-width:200px}
.order .total{font-weight:600;color:#1F3221}
.pill{display:inline-block;font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:.2rem .6rem;border-radius:999px;background:#EDF1EB;color:#42604A}
.pill.paid{background:#E4F0E2;color:#2F5A34}
.pill.pending{background:#FBF0DA;color:#7A5A17}
.pill.failed{background:#F8E3E1;color:#8A3A31}
label{display:block;font-size:.86rem;font-weight:600;color:#3D4F40;margin:0 0 .3rem}
input[type=email],input[type=password],.share{width:100%;box-sizing:border-box;border:1px solid #C6D2C2;border-radius:10px;padding:.7rem .85rem;font:inherit;background:#fff;color:#1C2A1F;margin:0 0 .9rem}
.err{color:#8A3A31;font-size:.92rem;margin:.2rem 0 .9rem;min-height:1.2em}
.hide{display:none}
{{ shared_nav_css|safe }}
</style>
</head>
<body>
{{ shared_nav_html|safe }}
<main class="wrap">
  <span class="eyebrow">Your account</span>
  <h1 id="hello">Your account</h1>
  <p class="sub" id="subline">Orders, plans and referral credit, all in one place.</p>

  <section id="signedOut" class="card hide">
    <h2>Sign in</h2>
    <p class="muted">Sign in to see your orders and manage your plan.</p>
    <form id="loginForm" autocomplete="on">
      <label for="email">Email</label>
      <input id="email" type="email" name="email" required autocomplete="email">
      <label for="password">Password</label>
      <input id="password" type="password" name="password" required autocomplete="current-password">
      <div class="err" id="loginErr" role="alert" aria-live="polite"></div>
      <button class="btn" type="submit" id="loginBtn">Sign in</button>
      <a class="btn ghost" href="/">Create an account</a>
    </form>
  </section>

  <div id="signedIn" class="hide">
    <section class="card">
      <h2>Orders <span class="muted" id="orderCount"></span></h2>
      <div id="orders"><p class="muted">Loading your orders...</p></div>
    </section>

    <section class="card hide" id="subsCard">
      <h2>Plans</h2>
      <div id="subs"></div>
    </section>

    <section class="card hide" id="refCard">
      <h2>Referral credit</h2>
      <div id="ref"></div>
    </section>

    <section class="card">
      <h2>Billing &amp; sign out</h2>
      <p class="muted" id="billingNote">Manage your payment method, invoices and subscriptions with Stripe.</p>
      <div class="row">
        <button class="btn" id="portalBtn" type="button">Manage billing</button>
        <button class="btn ghost" id="logoutBtn" type="button">Sign out</button>
      </div>
    </section>
  </div>
</main>

<script>
(function(){
  var $ = function(s){ return document.querySelector(s); };
  var show = function(el, on){ if (el) el.classList.toggle('hide', !on); };
  var esc = function(s){
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  };
  var money = function(cents){ return '$' + ((Number(cents) || 0) / 100).toFixed(2); };
  var when = function(iso){
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleDateString('en-US', { month:'short', day:'numeric', year:'numeric' });
  };
  var getJSON = function(url){
    return fetch(url, { credentials:'include' })
      .then(function(r){ return r.ok ? r.json() : null; })
      .catch(function(){ return null; });
  };

  function renderOrders(orders){
    var box = $('#orders');
    if (!orders || !orders.length){
      box.innerHTML = '<p class="muted">No orders yet. When you place one it shows up here with its status.</p>' +
                      '<a class="btn" href="/">Browse the shop</a>';
      return;
    }
    $('#orderCount').textContent = '(' + orders.length + ')';
    box.innerHTML = orders.map(function(o){
      var items = o.items;
      if (typeof items === 'string'){ try { items = JSON.parse(items); } catch(e){ items = []; } }
      if (!Array.isArray(items)) items = [];
      var names = items.map(function(i){
        var qty = Number(i.quantity) || 1;
        return esc(i.name) + (qty > 1 ? ' x' + qty : '');
      }).join(', ') || 'Order';
      var status = String(o.status || 'pending').toLowerCase();
      var cls = (status === 'paid' || status === 'shipped' || status === 'fulfilled') ? 'paid'
              : (status === 'failed' || status === 'refunded') ? 'failed' : 'pending';
      return '<div class="order">' +
               '<div class="items"><div>' + names + '</div>' +
                 '<div class="muted">#' + esc(o.id) + ' &middot; ' + esc(when(o.created_at)) + '</div></div>' +
               '<div><span class="pill ' + cls + '">' + esc(status) + '</span> ' +
                 '<span class="total">' + money(o.total_cents) + '</span></div>' +
             '</div>';
    }).join('');
  }

  function renderSubs(subs){
    if (!subs || !subs.length) return;
    show($('#subsCard'), true);
    $('#subs').innerHTML = subs.map(function(s){
      var status = String(s.status || '').toLowerCase();
      var cls = status === 'active' ? 'paid' : (status === 'canceled' ? 'failed' : 'pending');
      return '<div class="order"><div class="items">' + esc(s.product_name || 'Plan') + '</div>' +
             '<div><span class="pill ' + cls + '">' + esc(status || 'unknown') + '</span></div></div>';
    }).join('');
  }

  function renderRef(d){
    if (!d || !d.share_url) return;
    show($('#refCard'), true);
    var credit = Number(d.credit_balance_cents || 0);
    var amt = Math.round((d.referrer_credit_cents || 500) / 100);
    $('#ref').innerHTML =
      '<p class="muted">Every friend who orders earns you $' + amt +
      ' in crittr credit, applied automatically at your next checkout.' +
      (credit > 0 ? ' You have <strong>' + money(credit) + '</strong> waiting.' : '') + '</p>' +
      '<input class="share" type="text" id="shareUrl" readonly value="' + esc(d.share_url) + '">' +
      '<button class="btn ghost" type="button" id="copyBtn">Copy my link</button>';
    $('#copyBtn').addEventListener('click', function(){
      var i = $('#shareUrl'); i.select();
      try { document.execCommand('copy'); this.textContent = 'Copied'; } catch(e){}
    });
  }

  function loadSignedIn(user){
    $('#hello').textContent = user.name ? 'Hello, ' + user.name : 'Your account';
    $('#subline').textContent = user.email || 'Orders, plans and referral credit.';
    show($('#signedIn'), true);
    show($('#signedOut'), false);
    getJSON('/api/orders').then(function(d){ renderOrders(d && d.orders); });
    getJSON('/api/subscriptions').then(function(d){ renderSubs(d && d.subscriptions); });
    getJSON('/api/referrals/me').then(renderRef);
  }

  function boot(){
    getJSON('/api/me').then(function(d){
      if (d && d.user) return loadSignedIn(d.user);
      show($('#signedOut'), true);
      show($('#signedIn'), false);
    });
  }

  $('#loginForm').addEventListener('submit', function(e){
    e.preventDefault();
    var btn = $('#loginBtn'), err = $('#loginErr');
    err.textContent = ''; btn.disabled = true; btn.textContent = 'Signing in...';
    fetch('/api/login', {
      method:'POST', credentials:'include',
      headers:{ 'Content-Type':'application/json' },
      body: JSON.stringify({ email: $('#email').value, password: $('#password').value })
    }).then(function(r){ return r.json().then(function(j){ return { ok:r.ok, j:j }; }); })
      .then(function(res){
        if (res.ok && res.j.user) return loadSignedIn(res.j.user);
        err.textContent = (res.j && res.j.error) || 'Could not sign you in.';
      })
      .catch(function(){ err.textContent = 'Network error. Try again.'; })
      .then(function(){ btn.disabled = false; btn.textContent = 'Sign in'; });
  });

  $('#portalBtn').addEventListener('click', function(){
    var btn = this;
    btn.disabled = true; btn.textContent = 'Opening...';
    fetch('/api/portal', { method:'POST', credentials:'include' })
      .then(function(r){ return r.json().then(function(j){ return { ok:r.ok, j:j }; }); })
      .then(function(res){
        if (res.ok && res.j.url){ location.href = res.j.url; return; }
        // A 404 here is the ordinary case for a one-off buyer who has never had
        // a subscription, so say that instead of showing them an error.
        $('#billingNote').textContent = 'No billing profile yet - that starts with your first subscription.';
        btn.textContent = 'Manage billing';
      })
      .catch(function(){ btn.disabled = false; btn.textContent = 'Manage billing'; });
  });

  $('#logoutBtn').addEventListener('click', function(){
    fetch('/api/logout', { method:'POST', credentials:'include' })
      .then(function(){ location.href = '/'; })
      .catch(function(){ location.href = '/'; });
  });

  boot();
})();
</script>
{{ shared_nav_js|safe }}
</body>
</html>"""


def register_account_routes(app):
    """Wire GET /account.

    Flask matches a static rule ahead of a `<path:path>` converter regardless of
    registration order, so this takes precedence over the catch-all
    automatically. Worth stating out loud, since the entire reason this page
    exists is that the catch-all was answering for it.
    """

    @app.route("/account")
    def account_page():
        return render_template_string(
            _PAGE,
            shared_nav_css=SHARED_NAV_CSS,
            shared_nav_html=render_nav_html(""),
            shared_nav_js=SHARED_NAV_JS,
        )
