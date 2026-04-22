"""crittr.ai — SEO landing pages (Phase 7.1).

Renders `/c/<slug>` themed landing pages for common triage queries.
Each page:
  * Human-readable headline + 2-paragraph intro
  * Hero chat widget pre-filled with the topic
  * A 3-bullet "what to watch for" block
  * Internal links to 3 related topics
  * Shared footer (imported from the main template where possible)

Slug format
-----------
Kebab-case: "dog-ate-grapes", "cat-throwing-up-foam".
The slug maps to a `Topic` record with species + headline + triage
hints. Topics live in SEO_TOPICS below — seed with ~50 high-volume
long-tail queries; extend over time.

Public API
----------
    register_seo_landings(app) -> None
    TOPICS -> dict[slug, Topic]   (also exported for sitemap generation)
"""
import logging
from dataclasses import dataclass, field
from typing import List
from flask import render_template_string, abort, Response

log = logging.getLogger("crittr.seo")


# ---------------------------------------------------------------
# Topic catalog
# ---------------------------------------------------------------
@dataclass
class Topic:
    slug: str
    species: str         # "dog" | "cat"
    title: str           # "Dog ate grapes"
    question: str        # prefilled into the hero chat
    meta_description: str
    watch_for: List[str]
    lean: str            # "ER NOW" | "VET TOMORROW" | "SAFE AT HOME"
    related: List[str] = field(default_factory=list)


# Seed catalog. Copy is deliberately specific — thin pages don't rank.
_TOPICS = [
    Topic(
        slug="dog-ate-grapes",
        species="dog",
        title="My dog ate grapes — what now?",
        question="My dog ate grapes. What should I do?",
        meta_description=(
            "Your dog just ate grapes. Grapes are toxic to dogs at any dose. "
            "Here's exactly what to do in the next 30 minutes."
        ),
        watch_for=[
            "Vomiting or repeated retching within 2–4 hours",
            "Lethargy or refusal to drink in the next 12 hours",
            "Decreased urination over the next 24–48 hours (kidney sign)",
        ],
        lean="ER NOW",
        related=["dog-ate-chocolate", "dog-ate-xylitol", "puppy-not-eating"],
    ),
    Topic(
        slug="dog-ate-chocolate",
        species="dog",
        title="My dog ate chocolate — is it an emergency?",
        question="My dog ate chocolate. How much is too much?",
        meta_description=(
            "Chocolate toxicity in dogs is dose- and type-dependent. "
            "Dark chocolate is the worst. Here's how to decide when to rush in."
        ),
        watch_for=[
            "Restlessness, hyperactivity, or shaking",
            "Vomiting or diarrhea in the next 6 hours",
            "Rapid heart rate or unsteady walking",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-grapes", "dog-ate-xylitol", "dog-ate-onion"],
    ),
    Topic(
        slug="dog-ate-xylitol",
        species="dog",
        title="My dog ate xylitol — what to do",
        question="My dog ate something with xylitol in it. What now?",
        meta_description=(
            "Xylitol (found in sugar-free gum and peanut butter) causes "
            "rapid, life-threatening blood-sugar drops in dogs. Don't wait."
        ),
        watch_for=[
            "Weakness or collapse within 30–60 minutes",
            "Vomiting, tremors, or a seizure",
            "Disorientation or stumbling",
        ],
        lean="ER NOW",
        related=["dog-ate-grapes", "dog-ate-chocolate", "puppy-not-eating"],
    ),
    Topic(
        slug="cat-throwing-up-foam",
        species="cat",
        title="My cat is throwing up foam — should I worry?",
        question="My cat is throwing up foam. What does that mean?",
        meta_description=(
            "Foamy vomit in cats can be routine (hairball, empty stomach) "
            "or a red flag. Here's how to tell the difference."
        ),
        watch_for=[
            "More than 3 episodes in 24 hours",
            "Lethargy or refusal to eat past the next meal",
            "Any blood streaks in the foam",
        ],
        lean="SAFE AT HOME",
        related=["cat-not-eating", "cat-drinking-more-water",
                 "cat-hairball-every-day"],
    ),
    Topic(
        slug="dog-limping-after-walk",
        species="dog",
        title="My dog is limping after a walk — when is it serious?",
        question="My dog is limping after a walk. When should I go to the vet?",
        meta_description=(
            "Most limps that show up after a walk resolve with rest. Here's "
            "when it's time to stop monitoring and book a visit."
        ),
        watch_for=[
            "Swelling, heat, or a visible wound",
            "Limping that's worse the next morning",
            "Refusal to bear any weight at all",
        ],
        lean="VET TOMORROW",
        related=["dog-yelped-once", "puppy-not-eating", "dog-scratching-ear"],
    ),
    Topic(
        slug="puppy-not-eating",
        species="dog",
        title="My puppy isn't eating — what to check",
        question="My puppy hasn't eaten today. Should I be worried?",
        meta_description=(
            "Puppies skip meals for all kinds of reasons. Some are fine; some "
            "are dangerous. Here's what to check before deciding to wait."
        ),
        watch_for=[
            "No water intake either, especially under 3 months old",
            "Lethargy, cold gums, or sunken eyes",
            "Vomiting or diarrhea alongside",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-grapes", "dog-limping-after-walk",
                 "puppy-soft-stool"],
    ),
    Topic(
        slug="cat-not-eating",
        species="cat",
        title="My cat stopped eating — how long is too long?",
        question="My cat hasn't eaten in a day. When should I worry?",
        meta_description=(
            "Cats can develop fatty-liver disease from just a few days of "
            "not eating. The 24–48 hour window matters more than people think."
        ),
        watch_for=[
            "Yellowing of the gums or eyes",
            "Vomiting, hiding, or not drinking either",
            "Already underweight or a senior cat",
        ],
        lean="VET TOMORROW",
        related=["cat-throwing-up-foam", "cat-drinking-more-water",
                 "cat-hairball-every-day"],
    ),
]

TOPICS = {t.slug: t for t in _TOPICS}


# ---------------------------------------------------------------
# Template
# ---------------------------------------------------------------
_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ topic.title }} — crittr.ai</title>
  <meta name="description" content="{{ topic.meta_description }}">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <!-- Open Graph -->
  <meta property="og:type" content="article">
  <meta property="og:title" content="{{ topic.title }} — crittr.ai">
  <meta property="og:description" content="{{ topic.meta_description }}">
  <meta property="og:url" content="https://crittr.ai/c/{{ topic.slug }}">
  <meta property="og:image" content="https://crittr.ai/static/og/{{ topic.slug }}.png">
  <meta name="twitter:card" content="summary_large_image">
  <!-- JSON-LD -->
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "MedicalWebPage",
    "name": {{ topic.title | tojson }},
    "description": {{ topic.meta_description | tojson }},
    "about": {
      "@type": "MedicalCondition",
      "name": {{ topic.title | tojson }}
    },
    "audience": {
      "@type": "Audience",
      "audienceType": "Pet owners"
    }
  }
  </script>
  <style>
    :root {
      --bg:#FBF7EE; --ink:#2A2A2A; --muted:#6B6B6B; --accent:#6FA26F;
      --card:#FFFFFF; --line:#E7E1D2; --er:#C84A3A; --vet:#D9A23A;
    }
    body { margin:0; font-family:Inter,system-ui,sans-serif;
           background:var(--bg); color:var(--ink); line-height:1.55; }
    header { padding:24px 32px; border-bottom:1px solid var(--line); }
    header a { color:var(--ink); text-decoration:none; font-weight:500;
               font-family:'Fraunces',serif; }
    main { max-width:780px; margin:0 auto; padding:40px 24px; }
    h1 { font-family:'Fraunces',serif; font-weight:500; font-size:40px;
         line-height:1.15; margin:0 0 16px 0; }
    .lean { display:inline-block; padding:3px 10px; border-radius:999px;
            font-size:12px; letter-spacing:0.04em; text-transform:uppercase;
            font-weight:500; background:var(--line); color:var(--muted);
            margin-bottom:12px; }
    .lean.er { background:#FDF1EF; color:var(--er); }
    .lean.vet { background:#FCF6E8; color:var(--vet); }
    .lean.safe { background:#EEF5EA; color:var(--accent); }
    .card { background:var(--card); border:1px solid var(--line);
            border-radius:12px; padding:24px; margin:24px 0; }
    .hero-chat textarea {
      width:100%; box-sizing:border-box; padding:12px; font-size:16px;
      font-family:inherit; border:1px solid var(--line); border-radius:8px;
      resize:vertical; min-height:80px;
    }
    .hero-chat .send {
      background:var(--ink); color:white; border:none; padding:10px 20px;
      border-radius:8px; margin-top:12px; font-family:inherit; font-size:15px;
      cursor:pointer;
    }
    .watch-for h3 { margin:0 0 8px 0; font-size:15px;
                    letter-spacing:0.04em; text-transform:uppercase;
                    color:var(--muted); }
    .watch-for ul { margin:0; padding:0; list-style:none; }
    .watch-for li { padding:8px 0; border-bottom:1px dashed var(--line);
                    font-size:15px; }
    .watch-for li:last-child { border-bottom:none; }
    .related { display:grid; grid-template-columns:repeat(3, 1fr); gap:12px;
               margin-top:32px; }
    .related a { display:block; padding:12px; background:var(--card);
                 border:1px solid var(--line); border-radius:10px;
                 text-decoration:none; color:var(--ink); font-size:14px; }
    .related a:hover { border-color:var(--accent); }
    footer { text-align:center; padding:32px 24px; color:var(--muted);
             font-size:13px; border-top:1px solid var(--line); margin-top:40px; }
    @media(max-width:640px) {
      h1 { font-size:30px; }
      .related { grid-template-columns:1fr; }
    }
  </style>
</head>
<body>
  <header>
    <a href="/">crittr.ai</a>
  </header>
  <main>
    <span class="lean {{ lean_class }}">Tends to be: {{ topic.lean }}</span>
    <h1>{{ topic.title }}</h1>
    <p>{{ topic.meta_description }}</p>

    <div class="card hero-chat">
      <h3 style="margin:0 0 12px 0; font-family:'Fraunces',serif; font-weight:500;">
        Tell crittr what's happening
      </h3>
      <textarea id="q" placeholder="Describe the situation…">{{ topic.question }}</textarea>
      <button class="send" onclick="go()">Get a triage read</button>
    </div>

    <div class="card watch-for">
      <h3>What to watch for</h3>
      <ul>
        {% for w in topic.watch_for %}<li>{{ w }}</li>{% endfor %}
      </ul>
    </div>

    <div class="related">
      {% for slug in topic.related %}
        {% if slug in all_topics %}
          <a href="/c/{{ slug }}">{{ all_topics[slug].title }}</a>
        {% endif %}
      {% endfor %}
    </div>
  </main>
  <footer>
    crittr.ai is not a substitute for a veterinary exam.<br>
    In a true emergency, go to the nearest animal hospital.
  </footer>
  <script>
    // Posts to the anon hero-chat endpoint and replaces the card with the reply.
    async function go() {
      const q = document.getElementById('q').value.trim();
      if (!q) return;
      const wrap = document.querySelector('.hero-chat');
      wrap.innerHTML = '<p>Thinking…</p>';
      try {
        const r = await fetch('/api/chat/anon', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message: q, hint: {{ topic.species | tojson }}}),
        });
        const data = await r.json();
        wrap.innerHTML =
          '<div style="white-space:pre-wrap">' +
          (data.reply || '').replace(/</g, '&lt;') +
          '</div>';
      } catch (e) {
        wrap.innerHTML = '<p>Sorry — try again.</p>';
      }
    }
  </script>
</body>
</html>"""


_LEAN_CLASS = {"ER NOW": "er", "VET TOMORROW": "vet", "SAFE AT HOME": "safe"}


# ---------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------
def _sitemap_xml(base_url="https://crittr.ai"):
    urls = [f"{base_url}/"]
    urls.extend(f"{base_url}/c/{slug}" for slug in TOPICS)
    items = "".join(
        f"<url><loc>{u}</loc><changefreq>weekly</changefreq></url>"
        for u in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{items}</urlset>"
    )


# ---------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------
def register_seo_landings(app):
    """Wire GET /c/<slug>, GET /sitemap.xml."""
    @app.route("/c/<slug>")
    def seo_page(slug):
        topic = TOPICS.get(slug)
        if not topic:
            abort(404)
        return render_template_string(
            _HTML,
            topic=topic,
            all_topics=TOPICS,
            lean_class=_LEAN_CLASS.get(topic.lean, "safe"),
        )

    @app.route("/sitemap.xml")
    def sitemap():
        return Response(_sitemap_xml(), mimetype="application/xml")
