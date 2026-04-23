"""crittr.ai — baseline legal/policy pages required for Stripe live-mode review.

Routes
------
    GET /legal/refund-policy
    GET /legal/terms
    GET /legal/privacy
    GET /contact

Content is deliberately plain English and fair — specifically written to
satisfy Stripe's merchant acceptance review for consumer health/pet-pharma.
Have a lawyer review before scaling, but this is a defensible baseline.

Public API
----------
    register_legal_routes(app)
"""
from flask import render_template_string
from shared_nav import SHARED_NAV_CSS, SHARED_NAV_HTML, SHARED_NAV_JS


_BASE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ title }} — crittr.ai</title>
<meta name="description" content="{{ description }}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
body{margin:0;font-family:Inter,system-ui,sans-serif;background:#FDFBF5;color:#1C2A1F;line-height:1.65;-webkit-font-smoothing:antialiased}
h1,h2,h3{font-family:'Fraunces',serif;font-weight:600;color:#1F3221;line-height:1.2}
h1{font-size:clamp(2.2rem,4vw,3rem);margin:0 0 1rem}
h2{font-size:1.55rem;margin:2.2rem 0 .7rem}
h3{font-size:1.15rem;margin:1.3rem 0 .4rem;font-weight:600}
p{margin:0 0 1em;color:#3D4F40}
ul,ol{color:#3D4F40;padding-left:1.4rem;margin:0 0 1em}
li{margin:.4rem 0}
a{color:#3E6340;text-decoration:underline}
.legal-wrap{max-width:760px;margin:0 auto;padding:60px 24px 100px}
.legal-eyebrow{display:inline-block;font-size:.76rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:#527E54;margin-bottom:.9rem}
.legal-meta{color:#6E7D70;font-size:.9rem;margin:0 0 2.4rem;padding-bottom:1.4rem;border-bottom:1px solid #DFE5DB}
.legal-wrap p strong{color:#1F3221}
{{ shared_nav_css|safe }}
</style>
</head>
<body>
{{ shared_nav_html|safe }}
<main class="legal-wrap">
  <span class="legal-eyebrow">{{ eyebrow }}</span>
  <h1>{{ title }}</h1>
  <p class="legal-meta">Last updated {{ last_updated }}.</p>
  {{ body|safe }}
</main>
{{ shared_nav_js|safe }}
</body>
</html>"""


_REFUND_BODY = """
<p>We want you and your pet to be happy with every crittr order. If something isn't right, we'll make it right.</p>

<h2>The short version</h2>
<ul>
  <li><strong>OTC supplements, food, treats, collars:</strong> return within 30 days of delivery for a full refund, minus original shipping.</li>
  <li><strong>Damaged, incorrect, or defective items:</strong> full refund or free replacement at our expense — no return required. Email us within 7 days of delivery.</li>
  <li><strong>Prescription medications and prescription diets:</strong> non-returnable once shipped, per federal and state pharmacy regulations. We can refund or replace if the shipment is damaged in transit or if the wrong product was shipped.</li>
  <li><strong>Subscription orders:</strong> cancel any time before the next ship date and we won't charge you again. Already-shipped subscription orders follow the rules above.</li>
</ul>

<h2>How to request a refund or replacement</h2>
<p>Email <a href="mailto:hello@crittr.ai">hello@crittr.ai</a> with your order number and a short note about what went wrong. If it's a damaged or incorrect item, a photo helps us ship a replacement same-day.</p>
<p>Refunds post back to your original payment method within 5–10 business days after we process the return or approve the claim.</p>

<h2>What isn't refundable</h2>
<ul>
  <li>AI triage chats — these are always free, so there's nothing to refund.</li>
  <li>Vet consult fees charged by our partners (Vetster, AirVet, etc.) — each partner has its own refund policy, linked from the consult confirmation.</li>
  <li>Shipping fees on approved returns, unless the return was caused by our error.</li>
</ul>

<h2>Chargebacks and disputes</h2>
<p>If something goes wrong, please email us first — we can almost always resolve it faster than a chargeback, and a refund posts to your card sooner than a dispute does. Disputes filed without first contacting us may be contested.</p>

<h2>Contact</h2>
<p>Questions about a specific order: <a href="mailto:hello@crittr.ai">hello@crittr.ai</a>. For prescription-related questions, the fulfilling pharmacy is cc'd on your order confirmation.</p>
"""


_TERMS_BODY = """
<p>These terms govern your use of crittr.ai ("crittr", "we", "us"). By using the site, you agree to them. They're written in plain English on purpose — if anything is unclear, email <a href="mailto:hello@crittr.ai">hello@crittr.ai</a>.</p>

<h2>What crittr is — and isn't</h2>
<p>crittr is a consumer information service and retail marketplace. We provide AI-assisted triage for pet health questions, curated food and supplement sales, and referrals to independent licensed veterinary partners. <strong>crittr is not a veterinary clinic, pharmacy, or substitute for in-person veterinary care.</strong> Our AI triage does not diagnose or prescribe. For diagnosis, exams, labs, and prescriptions, you will always be referred to a licensed veterinarian.</p>

<h2>Who can use crittr</h2>
<p>You must be at least 18 and legally able to enter into a contract. crittr is intended for consumers in the United States; service availability outside the US is not guaranteed. Veterinary products and prescriptions ship only to eligible US addresses.</p>

<h2>Accounts</h2>
<p>You're responsible for activity on your account and for keeping your login credentials private. Don't share your account, don't impersonate someone else, and don't use crittr to harm or deceive others.</p>

<h2>Orders, pricing, and payment</h2>
<p>Prices shown at checkout are in US dollars. Taxes and shipping are calculated at checkout. We may cancel an order if a price is clearly listed in error, if we suspect fraud, or if we can't verify the shipping address. If we cancel, we refund in full.</p>
<p>Prescription items require a valid veterinary prescription obtained through our licensed partners. If a consult determines the requested prescription isn't right for your pet, we don't ship the item and you aren't charged.</p>

<h2>Subscriptions</h2>
<p>If you enroll in an auto-ship subscription (monthly, quarterly), we'll charge the payment method on file on each ship date until you cancel. Cancel from your account page or email us before the next ship date to skip that shipment.</p>

<h2>Returns and refunds</h2>
<p>Our refund rules are in the <a href="/legal/refund-policy">Refund Policy</a>.</p>

<h2>Partner services</h2>
<p>Vet consults are provided by independent licensed partners (Vetster, AirVet, and similar). Those providers set their own terms, charge their own fees, and are solely responsible for the clinical judgment they offer. crittr receives a referral fee from some partners when you use them; this doesn't change the price you pay.</p>

<h2>Intellectual property</h2>
<p>The crittr site, brand, copy, and original illustrations are owned by crittr. You may not scrape, reproduce, or resell them without written permission. User-generated content (your chat messages, pet profiles, reviews) remains yours; you grant us a non-exclusive license to display and use it to operate the service.</p>

<h2>Disclaimers</h2>
<p>crittr provides the service "as is." We don't warrant that the AI triage is error-free or that product information is always current. In a real emergency — collapse, heavy bleeding, seizure, breathing trouble — go to your nearest emergency vet immediately. Do not rely on crittr.</p>

<h2>Limitation of liability</h2>
<p>To the extent permitted by law, crittr is not liable for indirect, incidental, or consequential damages arising from use of the service. Our total liability for any claim is limited to the amount you paid us in the 12 months preceding the claim.</p>

<h2>Governing law</h2>
<p>These terms are governed by the laws of Delaware, USA. Disputes will be resolved in Delaware state or federal court, except where you have the right to use small-claims court.</p>

<h2>Changes</h2>
<p>We may update these terms. If changes are material, we'll email registered customers at least 14 days before they take effect. Continued use after the effective date means you accept the updated terms.</p>

<h2>Contact</h2>
<p><a href="mailto:hello@crittr.ai">hello@crittr.ai</a></p>
"""


_PRIVACY_BODY = """
<p>We collect the minimum we need to run crittr. We don't sell your data. We don't share it with ad networks. We use privacy-respecting vendors where we can.</p>

<h2>What we collect</h2>
<ul>
  <li><strong>Account info:</strong> email, password hash, name.</li>
  <li><strong>Pet profiles:</strong> species, breed, age, weight, allergies, meds, and anything else you choose to add.</li>
  <li><strong>Chat transcripts:</strong> your AI triage conversations, so we can improve the service and provide continuity next time you come back.</li>
  <li><strong>Order history:</strong> products, prices, shipping addresses, and payment method metadata (last 4 digits, card brand — never the full number).</li>
  <li><strong>Site usage:</strong> page views, referring URLs, approximate location (from IP), and device/browser, used to diagnose issues and improve the product.</li>
</ul>

<h2>What we don't collect</h2>
<p>We don't store full credit card numbers — Stripe handles all payment processing. We don't sync your contacts, scrape your email inbox, or track you across other websites.</p>

<h2>Who we share data with</h2>
<ul>
  <li><strong>Stripe</strong> — for payment processing. Stripe's privacy policy: <a href="https://stripe.com/privacy" target="_blank" rel="noopener">stripe.com/privacy</a>.</li>
  <li><strong>Chewy Pharmacy</strong> — for Rx fulfillment when you order a prescription.</li>
  <li><strong>Vetster, AirVet, and other vet partners</strong> — when you book a consult, we share the context you'd want the vet to have (pet profile, relevant chat history).</li>
  <li><strong>Resend</strong> — for transactional email (order confirmations, shipping updates).</li>
  <li><strong>OpenAI and Anthropic</strong> — for AI triage inference. We send only the chat content, not identifying info.</li>
  <li><strong>Law enforcement</strong> — only with a valid legal request. We'll notify you where legally allowed.</li>
</ul>

<h2>Your choices</h2>
<ul>
  <li><strong>Access or delete your data:</strong> email <a href="mailto:hello@crittr.ai">hello@crittr.ai</a> and we'll respond within 30 days.</li>
  <li><strong>Marketing emails:</strong> unsubscribe from any promotional email; transactional emails (order, shipping) will keep coming.</li>
  <li><strong>Cookies:</strong> we use a small number of first-party cookies to keep you logged in and remember your cart. You can clear them in your browser at any time.</li>
</ul>

<h2>California residents (CCPA) and other state privacy laws</h2>
<p>You have the right to know what personal info we collect, request a copy, and request deletion. Email us at <a href="mailto:hello@crittr.ai">hello@crittr.ai</a> with the subject "Privacy request" and we'll respond within 45 days.</p>

<h2>Children</h2>
<p>crittr isn't for children under 13. We don't knowingly collect info from children under 13. If you believe a child has given us info, email us and we'll delete it.</p>

<h2>Security</h2>
<p>We use industry-standard encryption in transit (HTTPS) and at rest (provider-managed). No system is perfectly secure — if a breach affects you, we'll notify you as required by law.</p>

<h2>Changes</h2>
<p>If we change this policy in a material way, we'll email registered customers at least 14 days before the change takes effect.</p>

<h2>Contact</h2>
<p><a href="mailto:hello@crittr.ai">hello@crittr.ai</a></p>
"""


_CONTACT_BODY = """
<p>The fastest way to reach us is email. A human reads every message.</p>

<h2>Get in touch</h2>
<ul>
  <li><strong>General, orders, billing:</strong> <a href="mailto:hello@crittr.ai">hello@crittr.ai</a></li>
  <li><strong>Privacy requests or account deletion:</strong> <a href="mailto:hello@crittr.ai">hello@crittr.ai</a> with the subject "Privacy request"</li>
  <li><strong>Press, partnerships:</strong> <a href="mailto:hello@crittr.ai">hello@crittr.ai</a></li>
</ul>
<p>Typical response time: under 24 hours on weekdays, by Monday for weekend emails.</p>

<h2>If your pet is in a real emergency right now</h2>
<p>Please don't wait for an email back. Go to your nearest emergency veterinary clinic, or call the ASPCA Animal Poison Control Center at (888) 426-4435 if you think your pet has been poisoned. crittr is not set up to handle real-time emergencies.</p>

<h2>Mailing address</h2>
<p>crittr.ai<br>[mailing address on file with Stripe]</p>
"""


def _page(title, eyebrow, description, body):
    return render_template_string(
        _BASE,
        title=title,
        eyebrow=eyebrow,
        description=description,
        body=body,
        last_updated="April 2026",
        shared_nav_css=SHARED_NAV_CSS,
        shared_nav_html=SHARED_NAV_HTML,
        shared_nav_js=SHARED_NAV_JS,
    )


def register_legal_routes(app):
    @app.route("/legal/refund-policy")
    def legal_refund():
        return _page(
            "Refund Policy",
            "Fair and fast",
            "crittr.ai's refund and return policy — what's refundable, how to request a refund, and timelines.",
            _REFUND_BODY,
        )

    @app.route("/legal/terms")
    def legal_terms():
        return _page(
            "Terms of Service",
            "The rules of the road",
            "crittr.ai Terms of Service — your agreement with us when you use the site.",
            _TERMS_BODY,
        )

    @app.route("/legal/privacy")
    def legal_privacy():
        return _page(
            "Privacy Policy",
            "What we collect, what we don't",
            "crittr.ai's privacy policy — what data we collect, who we share it with, and your rights.",
            _PRIVACY_BODY,
        )

    @app.route("/contact")
    def contact():
        return _page(
            "Contact us",
            "Get in touch",
            "Reach crittr.ai support — email, response times, and emergency guidance.",
            _CONTACT_BODY,
        )
