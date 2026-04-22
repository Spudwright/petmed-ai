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
    faqs: List[tuple] = field(default_factory=list)  # [(question, answer), ...] — empty = auto-generate


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
    Topic(
        slug="dog-ate-onion",
        species="dog",
        title="My dog ate onion — how serious is it?",
        question="My dog ate onion. What do I need to watch for?",
        meta_description=(
            "Onion contains thiosulfate, which damages a dog's red blood cells. "
            "Even small amounts can cause hemolytic anemia over a few days."
        ),
        watch_for=[
            "Pale gums or weakness 1–5 days after ingestion",
            "Dark brown or bloody urine",
            "Rapid breathing, reluctance to move",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-garlic", "dog-ate-grapes", "dog-ate-chocolate"],
    ),
    Topic(
        slug="dog-ate-garlic",
        species="dog",
        title="My dog ate garlic — is that toxic?",
        question="My dog ate garlic. Is that dangerous?",
        meta_description=(
            "Garlic is roughly 5x more toxic to dogs than onion by weight. "
            "Small single exposures often pass; repeated doses build up."
        ),
        watch_for=[
            "Vomiting or diarrhea in the first 24 hours",
            "Pale gums, lethargy, or collapse 1–5 days later",
            "Any breed with known sensitivity (Akita, Shiba Inu, Japanese breeds)",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-onion", "dog-ate-grapes", "dog-ate-chocolate"],
    ),
    Topic(
        slug="dog-ate-raisins",
        species="dog",
        title="My dog ate raisins — emergency?",
        question="My dog ate raisins. How much is dangerous?",
        meta_description=(
            "Raisins are concentrated grapes — dose for dose, more toxic. "
            "There's no established safe amount. Treat every exposure seriously."
        ),
        watch_for=[
            "Vomiting or retching within 2–4 hours",
            "Reduced urination in the next 24–48 hours",
            "Lethargy, wobbly walk, or bad breath (uremia)",
        ],
        lean="ER NOW",
        related=["dog-ate-grapes", "dog-ate-chocolate", "dog-ate-xylitol"],
    ),
    Topic(
        slug="dog-ate-avocado",
        species="dog",
        title="My dog ate avocado — should I worry?",
        question="My dog ate avocado. Is that toxic?",
        meta_description=(
            "The flesh of avocado is mostly fine for dogs in small amounts. "
            "The pit is the real risk — choking and intestinal obstruction."
        ),
        watch_for=[
            "Gagging or repeated retching (possible pit)",
            "Vomiting more than twice, or dry heaves",
            "Belly that feels tight or painful to touch",
        ],
        lean="SAFE AT HOME",
        related=["dog-ate-bone", "dog-ate-chocolate", "puppy-not-eating"],
    ),
    Topic(
        slug="dog-ate-ibuprofen",
        species="dog",
        title="My dog ate ibuprofen (Advil) — what now?",
        question="My dog ate an ibuprofen pill. What should I do?",
        meta_description=(
            "Ibuprofen is toxic to dogs at doses people consider normal. "
            "Even one 200mg tablet can injure the stomach of a small dog."
        ),
        watch_for=[
            "Vomiting (sometimes with blood) within a few hours",
            "Black, tarry stool over the next 1–2 days",
            "Decreased urination, lethargy (kidney sign)",
        ],
        lean="ER NOW",
        related=["dog-ate-acetaminophen", "dog-ate-xylitol", "dog-ate-grapes"],
    ),
    Topic(
        slug="dog-ate-acetaminophen",
        species="dog",
        title="My dog ate Tylenol (acetaminophen) — emergency?",
        question="My dog ate acetaminophen. Is that bad?",
        meta_description=(
            "Acetaminophen damages a dog's liver and red blood cells. "
            "Cats are even more sensitive — a single regular-strength tablet can kill."
        ),
        watch_for=[
            "Brown/blue gums or dark urine (methemoglobinemia)",
            "Vomiting, drooling, or loss of appetite",
            "Facial or paw swelling",
        ],
        lean="ER NOW",
        related=["dog-ate-ibuprofen", "cat-ate-lily", "dog-ate-xylitol"],
    ),
    Topic(
        slug="cat-ate-lily",
        species="cat",
        title="My cat chewed on a lily — what now?",
        question="My cat chewed on a lily plant. Is that dangerous?",
        meta_description=(
            "True lilies (Lilium and Hemerocallis) cause acute kidney failure in "
            "cats within 24–72 hours. Even pollen or vase water can do it."
        ),
        watch_for=[
            "Vomiting, drooling, or hiding within a few hours",
            "Reduced or no urination over 1–2 days",
            "Any plant material visible in the mouth or fur",
        ],
        lean="ER NOW",
        related=["cat-not-eating", "cat-throwing-up-foam", "dog-ate-grapes"],
    ),
    Topic(
        slug="dog-vomiting-yellow",
        species="dog",
        title="My dog is vomiting yellow foam — what does it mean?",
        question="My dog keeps throwing up yellow foam. Should I worry?",
        meta_description=(
            "Yellow foam is stomach bile. In dogs, an occasional morning "
            "episode is usually bilious vomiting syndrome and benign."
        ),
        watch_for=[
            "More than 2 episodes in 24 hours",
            "Any blood or coffee-ground material in the vomit",
            "Belly looks bloated or painful",
        ],
        lean="SAFE AT HOME",
        related=["dog-diarrhea", "dog-ate-bone", "puppy-not-eating"],
    ),
    Topic(
        slug="dog-diarrhea",
        species="dog",
        title="My dog has diarrhea — when do I need the vet?",
        question="My dog has diarrhea. How long should I wait?",
        meta_description=(
            "Most dog diarrhea resolves in 24–48 hours with a bland diet. "
            "A short list of red flags tells you when to stop waiting."
        ),
        watch_for=[
            "Frank red blood or black, tarry stool",
            "Lethargy, refusal to drink, or sunken eyes",
            "Puppy under 6 months, or any lasting past 48 hours",
        ],
        lean="SAFE AT HOME",
        related=["dog-vomiting-yellow", "puppy-not-eating", "dog-ate-bone"],
    ),
    Topic(
        slug="dog-ate-bone",
        species="dog",
        title="My dog swallowed a bone — is it dangerous?",
        question="My dog just swallowed a cooked bone. What should I do?",
        meta_description=(
            "Cooked bones splinter and can perforate the GI tract; raw bones "
            "tend to pass. Size of the bone vs size of the dog matters most."
        ),
        watch_for=[
            "Retching, drooling, or pawing at the mouth",
            "Refusal to eat or drink past the next meal",
            "Belly tense or painful; dark/bloody stool",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-avocado", "dog-vomiting-yellow", "dog-diarrhea"],
    ),
    Topic(
        slug="cat-constipation",
        species="cat",
        title="My cat is constipated — how long is too long?",
        question="My cat hasn't pooped in a couple days. What should I do?",
        meta_description=(
            "Occasional constipation is common in cats — chronic cases can "
            "progress to megacolon. The 72-hour mark is a useful threshold."
        ),
        watch_for=[
            "More than 72 hours without a bowel movement",
            "Straining in the litter box with little or no output",
            "Vomiting, lethargy, or bloated belly",
        ],
        lean="VET TOMORROW",
        related=["cat-not-eating", "cat-uti", "cat-throwing-up-foam"],
    ),
    Topic(
        slug="dog-shaking",
        species="dog",
        title="My dog is shaking — what could it be?",
        question="My dog is shaking and I don't know why. What should I check?",
        meta_description=(
            "Shaking in dogs can mean cold, fear, pain, nausea, or toxin "
            "exposure. Context — and what changed in the last 24 hours — matters."
        ),
        watch_for=[
            "Recent access to chocolate, xylitol, marijuana, or human meds",
            "Shaking plus vomiting, stumbling, or glazed eyes",
            "Shaking that doesn't stop with warmth and calm",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-chocolate", "dog-ate-xylitol", "dog-limping-after-walk"],
    ),
    Topic(
        slug="dog-ear-infection",
        species="dog",
        title="My dog has a smelly ear — is it an ear infection?",
        question="My dog's ear is smelly and he keeps scratching. What is it?",
        meta_description=(
            "Smelly discharge plus head shaking is almost always an ear "
            "infection. Waiting can push it from outer ear to middle ear."
        ),
        watch_for=[
            "Head tilt, balance trouble, or eye flicking (middle-ear spread)",
            "Dark debris that looks like coffee grounds (possible mites)",
            "Swollen, hot flap — can be an aural hematoma",
        ],
        lean="VET TOMORROW",
        related=["dog-hot-spot", "dog-limping-after-walk", "dog-diarrhea"],
    ),
    Topic(
        slug="dog-hot-spot",
        species="dog",
        title="My dog has a hot spot — what should I do tonight?",
        question="My dog has a raw, wet spot on his skin. How do I treat it?",
        meta_description=(
            "Hot spots (acute moist dermatitis) spread fast if left wet and "
            "licked. Keep it dry, keep it covered, and block the tongue."
        ),
        watch_for=[
            "Spreading redness past the original patch in 24 hours",
            "Yellow pus, thick crust, or fever (systemic infection)",
            "Hot spot on the ear flap — usually needs an oral antibiotic",
        ],
        lean="SAFE AT HOME",
        related=["dog-ear-infection", "dog-limping-after-walk", "dog-diarrhea"],
    ),
    Topic(
        slug="cat-uti",
        species="cat",
        title="My cat is straining in the litter box — UTI or blockage?",
        question="My cat is straining to pee. Is that a urinary blockage?",
        meta_description=(
            "A male cat straining with no urine output is an emergency — a "
            "full urethral blockage can be fatal within 24–48 hours."
        ),
        watch_for=[
            "No urine produced despite repeated straining (male cats)",
            "Crying in the box, licking genitals, vomiting",
            "Blood in urine or urinating outside the box",
        ],
        lean="ER NOW",
        related=["cat-not-eating", "cat-constipation", "cat-throwing-up-foam"],
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
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
      {% for q, a in faqs %}
      {
        "@type": "Question",
        "name": {{ q | tojson }},
        "acceptedAnswer": {
          "@type": "Answer",
          "text": {{ a | tojson }}
        }
      }{% if not loop.last %},{% endif %}
      {% endfor %}
    ]
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

    {% if faqs %}
    <div class="card faq">
      <h3 style="margin:0 0 12px 0; font-family:'Fraunces',serif; font-weight:500;">
        Common questions
      </h3>
      <div>
        {% for q, a in faqs %}
        <details style="border-bottom:1px dashed var(--line); padding:10px 0;">
          <summary style="cursor:pointer; font-weight:500; font-size:15px;">{{ q }}</summary>
          <p style="margin:8px 0 4px 0; font-size:14.5px; color:var(--muted); line-height:1.55;">{{ a }}</p>
        </details>
        {% endfor %}
      </div>
    </div>
    {% endif %}

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
# FAQ generation (FAQPage JSON-LD)
# ---------------------------------------------------------------
_LEAN_FAQ_BASE = {
    "ER NOW": (
        "Should I go to the emergency vet right now?",
        "Yes. Based on what you\'ve described, this is the kind of situation where minutes can matter. "
        "Call your nearest 24/7 animal ER on the way. If you\'re not sure where that is, use our free triage chat above — "
        "it\'ll confirm the urgency and surface a local ER in seconds.",
    ),
    "VET TOMORROW": (
        "Can this wait until the morning?",
        "In most cases, yes — a same-day or next-morning vet visit is the right call, not an ER run. "
        "Watch the signs listed above. If any of them escalate overnight, escalate too. When in doubt, start the chat.",
    ),
    "SAFE AT HOME": (
        "Is this actually an emergency?",
        "Most of the time, no — this category is usually safe to manage at home with monitoring. "
        "That said, every critter is different. Run your specifics through the chat above for a read on your pet, not the average.",
    ),
}


def _build_faqs(topic):
    """Return a list of (question, answer) tuples for this topic."""
    if topic.faqs:
        return list(topic.faqs)
    faqs = []
    # 1) Lean-specific question
    lean_q = _LEAN_FAQ_BASE.get(topic.lean)
    if lean_q:
        faqs.append(lean_q)
    # 2) Watch-for question
    if topic.watch_for:
        signs = "; ".join(topic.watch_for[:3])
        faqs.append((
            "What symptoms should I watch for?",
            f"Three signs worth watching in the next 24\u201348 hours: {signs}. "
            "If any of them show up or get worse, move up one tier (home \u2192 vet, vet \u2192 ER).",
        ))
    # 3) Vet visit cost + teletriage hook
    faqs.append((
        "Do I need to pay for a vet visit just to ask?",
        "No. Our triage chat is free \u2014 it\'ll tell you whether a vet visit is actually warranted before you spend anything. "
        "If you do need a licensed vet, we connect you to one via Vetster or AirVet in minutes, from your phone.",
    ))
    # 4) crittr pharmacy hook
    faqs.append((
        "Can crittr fill a prescription for this?",
        "If a licensed vet prescribes meds during or after triage, yes \u2014 Rx orders are routed through our licensed pharmacy partner (Chewy Pharmacy). "
        "You can also browse our OTC picks directly; we only stock items our vet advisors actually recommend.",
    ))
    return faqs

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
            faqs=_build_faqs(topic),
        )

    @app.route("/sitemap.xml")
    def sitemap():
        return Response(_sitemap_xml(), mimetype="application/xml")
