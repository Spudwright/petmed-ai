"""crittr.ai - a real 404, and the short list of paths that are not one.

The problem this fixes
----------------------
`app.catch_all` answered EVERY unmatched path with the marketing homepage and
an HTTP 200. `/this-page-does-not-exist` was a 200. `/shop` was a 200. A typo in
an email link was a 200. That is a soft 404, and it costs three ways:

  1. Search engines index unbounded junk URLs as duplicates of the homepage,
     which is a real cost for a site whose whole problem is acquisition.
  2. Broken links become invisible. Nothing anywhere - not a log, not a status
     code, not the page itself - distinguishes a working link from a dead one,
     so dead ones survive indefinitely. Two shipped in the sitewide nav.
  3. It makes the site unverifiable from outside: you cannot check whether a
     route exists by asking for it, which is exactly the trap that made route
     probing on crittr unreliable.

Why an allow-list rather than "404 everything unmatched"
--------------------------------------------------------
The SPA does own a couple of paths client-side, and Flask cannot know that. So
the catch-all still serves the SPA for those - and ONLY those. Anything else
unmatched is now honestly a 404.

Keep _SPA_PATHS in sync with `crittrRoute()` in static/index-v2.html. If you add
a client-side route there and not here, it starts 404ing; there is no clever way
around that, because the server genuinely cannot see the SPA's router.

Public API
----------
    SPA_PATHS         paths the SPA handles client-side
    render_404()      the page, for use by the catch-all
    register_not_found(app)   wires Flask's own 404 handler to the same page
"""
from flask import render_template_string
from shared_nav import SHARED_NAV_CSS, SHARED_NAV_JS, render_nav_html


# Normalised WITHOUT a leading slash, which is how Flask's <path:path> hands
# them over.
SPA_PATHS = {
    # The post-checkout screen. crittrRoute() activates #view-success on this
    # exact path, so it must reach the SPA rather than a 404.
    "order/success",

    # "Save profile" and the drawer's create-account CTA both point here. The
    # SPA opens its register modal on this path.
    "signup",

}


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Page not found - crittr.ai</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,follow">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
body{margin:0;font-family:Inter,system-ui,sans-serif;background:#FDFBF5;color:#1C2A1F;line-height:1.65;-webkit-font-smoothing:antialiased}
h1{font-family:'Fraunces',serif;font-weight:600;color:#1F3221;font-size:clamp(2.1rem,4vw,2.9rem);margin:0 0 .6rem;line-height:1.15}
p{margin:0 0 1.4em;color:#3D4F40;font-size:1.05rem}
a{color:#3E6340}
.wrap{max-width:620px;margin:0 auto;padding:90px 24px 120px;text-align:center}
.eyebrow{display:inline-block;font-size:.76rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:#527E54;margin-bottom:1rem}
.btn{display:inline-block;border-radius:999px;padding:.75rem 1.5rem;font-weight:600;font-size:.97rem;text-decoration:none;background:#3E6340;color:#fff;margin:.25rem}
.btn:hover{background:#33532F}
.btn.ghost{background:transparent;color:#3E6340;border:1px solid #C6D2C2}
.btn.ghost:hover{background:#F1F5EF}
.links{margin-top:2.6rem;padding-top:1.6rem;border-top:1px solid #DFE5DB;font-size:.95rem;color:#6E7D70}
.links a{margin:0 .5rem;white-space:nowrap}
{{ shared_nav_css|safe }}
</style>
</head>
<body>
{{ shared_nav_html|safe }}
<main class="wrap">
  <span class="eyebrow">404</span>
  <h1>We couldn't find that page</h1>
  <p>The link may be old, or we may have moved it. Your pet is fine; the URL isn't.</p>
  <a class="btn" href="/">Go to the homepage</a>
  <a class="btn ghost" href="/#hero-chat">Ask about a symptom</a>
  <div class="links">
    <a href="/shop/dogs">Dogs</a>
    <a href="/shop/cats">Cats</a>
    <a href="/account">Your account</a>
    <a href="/contact">Contact us</a>
  </div>
</main>
{{ shared_nav_js|safe }}
</body>
</html>"""


def render_404():
    """The 404 body and status, as a Flask response tuple.

    JSON for `/api/*`, the page for everyone else. The decision lives HERE
    rather than in the error handler because the `<path:path>` catch-all
    matches `/api/nope` itself and returns directly - so a check that lived
    only in the handler would never run for the API paths it was written for.
    """
    from flask import request, jsonify

    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404

    return (
        render_template_string(
            _PAGE,
            shared_nav_css=SHARED_NAV_CSS,
            shared_nav_html=render_nav_html(""),
            shared_nav_js=SHARED_NAV_JS,
        ),
        404,
    )


def register_not_found(app):
    """Use the same page for Flask's own 404s (wrong method, bad converter)."""

    @app.errorhandler(404)
    def _not_found(_e):
        return render_404()
