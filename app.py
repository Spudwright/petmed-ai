"""
Crittr — AI-Powered Pet Pharmacy
Fully automated: AI chatbot, product recommendations, content generation, operations.
Flask + PostgreSQL + OpenAI. Deployed on Railway.
"""

import os
import json
import hashlib
import secrets
import time
from pathlib import Path
from functools import wraps
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, session, redirect, url_for, send_from_directory
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from stripe_routes import register_stripe_routes
from pets_routes import register_pets_routes
# --- Phase 6: AI operations ---
from llm_client import set_fallback_observer
from admin_dashboard import register_admin_dashboard
from events import register_event_routes
from alerts import record_fallback
# --- Phase 7: content, viral, regional ---
from seo_landings import register_seo_landings
from find_vet import register_find_vet_routes
from referrals import register_referral_routes
from shop_routes import register_shop_routes
from product_images import ensure_product_images
from crittr_rx_rebrand import ensure_rx_rebrand, register_rx_rebrand_redirects
from og_images import register_og_routes
from legal_routes import register_legal_routes
from regions import register_region_middleware
try:
    from youtube import youtube_bp
except Exception:  # pragma: no cover
    youtube_bp = None

load_dotenv()

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_MODEL_PREMIUM = os.environ.get("OPENAI_MODEL_PREMIUM", "gpt-4o")
OPENAI_MODEL_CHEAP = os.environ.get("OPENAI_MODEL_CHEAP", "gpt-4o-mini")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
SITE_NAME = "Crittr"
SITE_TAGLINE = "Your AI-Powered Pet Pharmacy"

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
import psycopg2
from psycopg2.extras import RealDictCursor

def get_db():
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def q(sql, params=None, fetch=True):
    conn = get_db()
    if not conn:
        return [] if fetch else None
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        if fetch:
            return cur.fetchall()
        return None
    finally:
        conn.close()

def q1(sql, params=None):
    rows = q(sql, params)
    return rows[0] if rows else None

def init_db():
    conn = get_db()
    if not conn:
        print("No DATABASE_URL — running without database")
        return
    try:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT DEFAULT '',
            role TEXT DEFAULT 'customer',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS pets (
            id SERIAL PRIMARY KEY,
            user_id INT REFERENCES users(id),
            name TEXT NOT NULL,
            species TEXT DEFAULT 'dog',
            breed TEXT DEFAULT '',
            weight_lbs REAL,
            age_years REAL,
            conditions TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS categories (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '\U0001f48a',
            sort_order INT DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            category_id INT REFERENCES categories(id),
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            ai_description TEXT DEFAULT '',
            price_cents INT NOT NULL,
            compare_price_cents INT,
            image_url TEXT DEFAULT '',
            species TEXT DEFAULT 'dog,cat',
            requires_rx BOOLEAN DEFAULT FALSE,
            in_stock BOOLEAN DEFAULT TRUE,
            tags TEXT DEFAULT '',
            dosage_info TEXT DEFAULT '',
            warnings TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id INT REFERENCES users(id),
            status TEXT DEFAULT 'pending',
            items JSONB DEFAULT '[]',
            subtotal_cents INT DEFAULT 0,
            tax_cents INT DEFAULT 0,
            shipping_cents INT DEFAULT 0,
            total_cents INT DEFAULT 0,
            shipping_address JSONB DEFAULT '{}',
            stripe_payment_id TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS chat_logs (
            id SERIAL PRIMARY KEY,
            user_id INT,
            session_id TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        print("Database tables initialized.")
        seed_products(cur)
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        conn.close()


def seed_products(cur):
    """Seed categories and products if empty."""
    cur.execute("SELECT COUNT(*) as c FROM categories")
    if cur.fetchone()["c"] > 0:
        return

    categories = [
        ("Flea & Tick", "flea-tick", "Preventive treatments for fleas, ticks, and parasites", "\U0001f6e1\ufe0f", 1),
        ("Heartworm", "heartworm", "Monthly heartworm prevention medications", "\u2764\ufe0f", 2),
        ("Joint & Mobility", "joint-mobility", "Supplements and medications for joint health", "\U0001f9b4", 3),
        ("Anxiety & Calming", "anxiety-calming", "Calming supplements and anxiety relief", "\U0001f9d8", 4),
        ("Digestive Health", "digestive", "Probiotics, enzymes, and digestive support", "\U0001fac4", 5),
        ("Skin & Coat", "skin-coat", "Supplements for healthy skin and shiny coat", "\u2728", 6),
        ("Dental Care", "dental", "Dental chews, rinses, and oral health products", "\U0001f9b7", 7),
        ("Vitamins & Supplements", "vitamins", "Daily vitamins and nutritional supplements", "\U0001f48a", 8),
    ]
    for name, slug, desc, icon, sort in categories:
        cur.execute("INSERT INTO categories (name,slug,description,icon,sort_order) VALUES (%s,%s,%s,%s,%s)",
                    (name, slug, desc, icon, sort))

    products = [
        # Flea & Tick
        ("NexGard Plus", "nexgard-plus", 1, 4999, 5999, "dog", True,
         "Monthly chewable that kills fleas, ticks, and prevents heartworm. For dogs 24-60 lbs.",
         "Give one chew monthly. For dogs 24.1-60 lbs only.", "Not for use in cats. Keep out of reach of children."),
        ("Frontline Gold", "frontline-gold", 1, 3899, 4499, "dog,cat", False,
         "Topical flea and tick treatment. Kills fleas, flea eggs, larvae, and ticks for 30 days.",
         "Apply to skin between shoulder blades monthly.", "Avoid bathing 24hrs before/after application."),
        ("Seresto Collar", "seresto-collar", 1, 5499, 6499, "dog,cat", False,
         "8-month flea and tick prevention collar. Odorless, non-greasy, water-resistant.",
         "Replace every 8 months. Adjust to fit with 2-finger gap.", "Remove if skin irritation occurs."),
        # Heartworm
        ("Heartgard Plus", "heartgard-plus", 2, 4599, 5299, "dog", True,
         "Monthly chewable heartworm preventive that also treats and controls roundworms and hookworms.",
         "Give one chew monthly, year-round.", "Dogs should be tested for heartworm before starting."),
        ("Revolution Plus", "revolution-plus", 2, 5299, None, "cat", True,
         "Monthly topical that prevents heartworm and kills fleas, ticks, ear mites, roundworms, and hookworms in cats.",
         "Apply monthly to skin at base of neck.", "For cats only. Do not use on dogs."),
        # Joint & Mobility
        ("Cosequin DS Plus MSM", "cosequin-ds-msm", 3, 3299, 3999, "dog", False,
         "Joint health supplement with glucosamine, chondroitin, and MSM for dogs.",
         "Loading: 1-3 tablets daily based on weight. Maintenance: half dose.", ""),
        ("Dasuquin Advanced", "dasuquin-advanced", 3, 5499, None, "dog", False,
         "Advanced joint supplement with ASU, glucosamine, and chondroitin. Veterinarian recommended.",
         "See weight-based dosing chart.", ""),
        # Anxiety & Calming
        ("Composure Pro", "composure-pro", 4, 2499, 2999, "dog,cat", False,
         "Calming chews with colostrum, L-theanine, and thiamine. Supports calm behavior during stress.",
         "1-3 chews based on weight. Can double for acute stress.", ""),
        ("Adaptil Calm Diffuser", "adaptil-calm", 4, 3499, None, "dog", False,
         "Pheromone diffuser that releases dog-appeasing pheromone. Covers up to 700 sq ft.",
         "Plug in and leave on continuously. Replace refill every 30 days.", "For dogs only."),
        # Digestive
        ("FortiFlora Probiotic", "fortiflora", 5, 3099, 3499, "dog,cat", False,
         "Veterinary-strength probiotic supplement. Promotes intestinal health and balance.",
         "Sprinkle one packet on food daily.", ""),
        ("Purina Pro Plan Veterinary Diets EN", "purina-en", 5, 4299, None, "dog", True,
         "Prescription gastroenteric formula for dogs with digestive issues.",
         "Feed according to weight chart. Transition over 7 days.", "Prescription required."),
        # Skin & Coat
        ("Welactin Omega-3", "welactin-omega3", 6, 2899, 3299, "dog,cat", False,
         "Liquid omega-3 supplement from cold-water fish. Supports healthy skin, coat, and joints.",
         "Pump onto food daily. See weight-based dosing.", ""),
        # Dental
        ("Greenies Original", "greenies-original", 7, 2199, 2499, "dog", False,
         "Dental chews that clean teeth, freshen breath, and are highly digestible.",
         "One chew daily for dogs 25-50 lbs.", "Supervise while chewing."),
        ("Oravet Dental Hygiene Chews", "oravet-chews", 7, 2999, None, "dog", False,
         "Dual-action dental chew with delmopinol that creates a barrier against plaque and bacteria.",
         "One chew daily.", "For dogs 25-50 lbs."),
        # Vitamins
        ("Pet-Tabs Plus", "pet-tabs-plus", 8, 1899, 2199, "dog", False,
         "Daily multivitamin with minerals, amino acids, and fatty acids for dogs.",
         "One tablet daily for dogs up to 50 lbs. Two for larger dogs.", ""),
        ("VetriScience Nu Cat", "nucat-multivitamin", 8, 1499, None, "cat", False,
         "Complete multivitamin for cats. Supports immune, digestive, and overall health.",
         "One chew daily for adult cats.", ""),
    ]

    for name, slug, cat_id, price, compare, species, rx, desc, dosage, warnings in products:
        cur.execute("""INSERT INTO products
            (name,slug,category_id,price_cents,compare_price_cents,species,requires_rx,description,dosage_info,warnings)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (name, slug, cat_id, price, compare, species, rx, desc, dosage, warnings))

    print(f"Seeded {len(categories)} categories and {len(products)} products.")

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def hash_pw(pw):
    return generate_password_hash(pw)

def check_pw(pw, hashed):
    return check_password_hash(hashed, pw)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Login required"}), 401
        return f(*args, **kwargs)
    return decorated

# ---------------------------------------------------------------------------
# AI Engine
# ---------------------------------------------------------------------------
# Module-level cache for the long stable system prompt. Built lazily on first
# call, kept for the lifetime of the process so OpenAI's automatic prompt
# caching (>=1024 identical-prefix tokens) kicks in and cuts input cost ~10x.
_CACHED_SYSTEM_PROMPT = None


def _build_default_system_prompt():
    """Build a long, stable, cache-friendly system prompt with the live product
    catalog inlined. Cached for the process lifetime - restart the container
    (any redeploy) to refresh after catalog changes."""
    global _CACHED_SYSTEM_PROMPT
    if _CACHED_SYSTEM_PROMPT is not None:
        return _CACHED_SYSTEM_PROMPT

    try:
        products = q("SELECT id,name,description,price_cents,species,requires_rx FROM products WHERE in_stock=TRUE ORDER BY id") or []
    except Exception:
        products = []
    lines = []
    for p in products:
        rx = " [Rx required]" if p.get("requires_rx") else ""
        species = (p.get("species") or "any species").strip()
        desc = (p.get("description") or "").strip().replace("\n", " ")[:200]
        lines.append(f"  - [#{p['id']}] {p['name']} - ${p['price_cents']/100:.2f} - for {species}{rx}\n    {desc}")
    catalog = "\n".join(lines) if lines else "  (catalog currently empty)"

    prompt = f"""# HARD TONE RULES (override everything else below)
- Keep every reply to 2-3 short sentences unless the user explicitly asks for more detail.
- NEVER list products, prices, dosages, or treatment protocols in your FIRST reply to a concern.
- For any symptom or concern, your FIRST reply must: (1) briefly acknowledge in one sentence, (2) ask ONE simple clarifying question, (3) offer: "Would you like me to give you some suggestions on what might help your pet?"
- Only recommend specific products AFTER the user says yes or explicitly asks for product recommendations.
- Write in plain conversational prose. No bullet lists, no markdown headers, no bold/italic, no asterisks.
- No emergency triage paragraphs, no long disclaimers, no "consult your vet" boilerplate unless the user asks.
- Sound like a calm knowledgeable friend, not a medical pamphlet.

You are the AI veterinary pharmacy assistant for {SITE_NAME} ({SITE_TAGLINE}).

# Identity & Voice
You are warm, professional, knowledgeable, and genuinely caring. You treat every pet owner like a friend who just walked into the clinic worried about their animal. You're never clinical-cold or robotic - you acknowledge the emotional weight of pet health. You use plain language, avoid jargon unless you define it, and you're concise without being curt.

# Core Operating Rules
1. ALWAYS recommend consulting a licensed veterinarian for any serious, urgent, or persistent concern. You are a pharmacy assistant, not a diagnostician.
2. NEVER definitively diagnose a medical condition. Offer possibilities ("this could be...") and point toward a vet visit when the situation warrants.
3. For OTC (over-the-counter) products you CAN suggest specific items from the catalog below based on described symptoms - flea/tick, joint support, digestive, skin, dental, calming, ear care, etc.
4. For Rx (prescription) products explain that a valid veterinary prescription is required - we verify prescriptions before shipping.
5. NEVER invent medications or dosages. Only discuss products in the catalog or commonly-known generics. For dosing of anything species-specific, defer to the vet.
6. Flag red-flag symptoms clearly: collapse, seizures, bloat/distended abdomen, labored breathing, pale gums, suspected toxin ingestion (chocolate, xylitol, grapes, lily, human meds), hit-by-car, open wounds, inability to urinate, uncontrolled bleeding, prolonged vomiting/diarrhea. In any of these cases: urge immediate emergency vet contact BEFORE suggesting any product.
7. Respect species differences - NEVER recommend a dog product for a cat (or vice versa) without explicitly flagging it. Cats metabolize many drugs very differently (acetaminophen, permethrin, NSAIDs are classic danger zones).
8. Pricing, stock, and product IDs live in the catalog below - when you recommend a product, name it exactly as listed and include the price.

# Safety Boundaries
- You do not provide dosing for human medications repurposed for pets beyond "ask your vet".
- You do not discuss euthanasia procedures or end-of-life medication dosing.
- You do not comment on another vet's diagnosis or treatment plan.
- If a user seems distressed about a pet in crisis, lead with empathy and the emergency-vet pointer before anything else.

# Product Catalog (live inventory)
{catalog}

# Response Format
- Default to 2-4 short paragraphs. Use a short list only when comparing products or listing symptoms to watch for.
- When recommending a product, include: name, price, why it fits, and any caveats (species, Rx status, "ask your vet first if...").
- End substantive clinical replies with a gentle "If this doesn't improve in X days, or if you notice Y, please see your vet."

# Brand Voice Examples
Pet owner: "my dog has been scratching a lot"
You: Scratching can have a bunch of causes - fleas, seasonal allergies, a food sensitivity, or dry skin being the common ones. A couple of things that'd help me point you in the right direction: any fleas/flea dirt visible, any hotspots or broken skin, and whether the scratching is mostly in one spot or all over?

If it's flea-related, [product]. If it's more of an allergic/irritation pattern, [product]. If you see open sores, a raw patch, or your dog seems miserable, please book a vet visit - persistent scratching can escalate to infection.

Pet owner: "cat threw up three times today"
You: Three episodes in a day is worth watching closely, especially if there's any lethargy, refusing food/water, or blood in the vomit - in any of those cases, please call your vet today. If she's otherwise bright and the vomit looks like hair or food, try pulling food for 12 hours (water still available) then reintroduce a small bland meal. If the vomiting continues past 24 hours, or you see any of the red flags above, that's a vet visit.

Pet owner: "what's the best joint supplement"
You: [specific product from catalog with price], because [reason]. Starts working for most dogs around 4-6 weeks in. If your dog has kidney issues or is already on NSAIDs, check with your vet first.

Now respond to the user's question. Be specific, be warm, and be useful."""
    _CACHED_SYSTEM_PROMPT = prompt
    return prompt


def _resolve_model(tier="default"):
    """Pick a model based on the requested tier. All names are env-driven so
    you can swap without redeploying code."""
    if tier == "premium":
        return OPENAI_MODEL_PREMIUM
    if tier == "cheap":
        return OPENAI_MODEL_CHEAP
    return OPENAI_MODEL


def ai_chat(messages, system_prompt=None, tier="default"):
    """Call OpenAI for chat completion.

    tier: 'cheap' | 'default' | 'premium'. Routing decisions live at the call
    site - a short FAQ answer uses 'cheap', a complex triage uses 'premium'.
    Passing system_prompt explicitly bypasses the cached default prompt.
    """
    if not OPENAI_API_KEY:
        return "AI features require an OpenAI API key. Please configure OPENAI_API_KEY."
    try:
        import openai
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        sys_msg = system_prompt or _build_default_system_prompt()
        full_messages = [{"role": "system", "content": sys_msg}] + messages
        resp = client.chat.completions.create(
            model=_resolve_model(tier),
            messages=full_messages,
            max_tokens=800,
            temperature=0.5,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"Sorry, I'm having trouble connecting right now. Please try again. ({str(e)[:200]})"


def ai_product_recommendation(pet_info, symptoms):
    """AI recommends products based on pet info and symptoms."""
    products = q("SELECT id,name,description,price_cents,species,requires_rx,category_id FROM products WHERE in_stock=TRUE")
    product_list = "\n".join([f"- {p['name']} (${p['price_cents']/100:.2f}): {p['description'][:100]}" for p in (products or [])])

    prompt = f"""Based on the following pet information and symptoms, recommend the top 3 most appropriate products from our catalog.
Return JSON array with product IDs and reasoning.

Pet: {json.dumps(pet_info)}
Symptoms/Needs: {symptoms}

Available products:
{product_list}

Return ONLY valid JSON: [{{"product_id": 1, "reason": "brief reason"}}, ...]"""

    messages = [{"role": "user", "content": prompt}]
    response = ai_chat(messages, system_prompt="You are a veterinary product recommendation engine. Return only valid JSON arrays.")

    try:
        start = response.index("[")
        end = response.rindex("]") + 1
        return json.loads(response[start:end])
    except:
        return []

# ---------------------------------------------------------------------------
# API Routes — Auth
# ---------------------------------------------------------------------------
@app.route("/api/register", methods=["POST"])
def api_register():
    d = request.json or {}
    email = d.get("email", "").strip().lower()
    password = d.get("password", "")
    name = d.get("name", "")
    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    existing = q1("SELECT id FROM users WHERE email=%s", (email,))
    if existing:
        return jsonify({"error": "Email already registered"}), 400
    pw_hash = hash_pw(password)
    user = q1("INSERT INTO users (email,password_hash,name) VALUES (%s,%s,%s) RETURNING id,email,name,role",
              (email, pw_hash, name))
    session["user_id"] = user["id"]
    return jsonify({"user": dict(user)})

@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.json or {}
    email = d.get("email", "").strip().lower()
    password = d.get("password", "")
    user = q1("SELECT id,email,name,role,password_hash FROM users WHERE email=%s", (email,))
    if not user or not check_pw(password, user["password_hash"]):
        return jsonify({"error": "Invalid email or password"}), 401
    session["user_id"] = user["id"]
    return jsonify({"user": {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/me")
def api_me():
    if "user_id" not in session:
        return jsonify({"user": None})
    user = q1("SELECT id,email,name,role FROM users WHERE id=%s", (session["user_id"],))
    return jsonify({"user": dict(user) if user else None})

# ---------------------------------------------------------------------------
# API Routes — Products
# ---------------------------------------------------------------------------
@app.route("/api/categories")
def api_categories():
    cats = q("SELECT * FROM categories ORDER BY sort_order")
    return jsonify({"categories": [dict(c) for c in (cats or [])]})

@app.route("/api/products")
def api_products():
    category = request.args.get("category")
    species = request.args.get("species")
    search = request.args.get("q")

    sql = "SELECT p.*, c.name as category_name, c.slug as category_slug FROM products p JOIN categories c ON p.category_id=c.id WHERE p.in_stock=TRUE"
    params = []
    if category:
        sql += " AND c.slug=%s"
        params.append(category)
    if species:
        sql += " AND p.species ILIKE %s"
        params.append(f"%{species}%")
    if search:
        sql += " AND (p.name ILIKE %s OR p.description ILIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])
    sql += " ORDER BY c.sort_order, p.name"

    products = q(sql, params)
    return jsonify({"products": [dict(p) for p in (products or [])]})

@app.route("/api/products/<slug>")
def api_product_detail(slug):
    product = q1("""SELECT p.*, c.name as category_name, c.slug as category_slug
        FROM products p JOIN categories c ON p.category_id=c.id WHERE p.slug=%s""", (slug,))
    if not product:
        return jsonify({"error": "Product not found"}), 404
    return jsonify({"product": dict(product)})

# ---------------------------------------------------------------------------
# API Routes — AI Chat
# ---------------------------------------------------------------------------
@app.route("/api/chat", methods=["POST"])
def api_chat():
    d = request.json or {}
    message = d.get("message", "").strip()
    history = d.get("history", [])
    if not message:
        return jsonify({"error": "Message required"}), 400

    messages = []
    for h in history[-10:]:
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content": message})

    reply = ai_chat(messages)

    user_id = session.get("user_id")
    sid = d.get("session_id", secrets.token_hex(8))
    try:
        q("INSERT INTO chat_logs (user_id,session_id,role,content) VALUES (%s,%s,'user',%s)",
          (user_id, sid, message), fetch=False)
        q("INSERT INTO chat_logs (user_id,session_id,role,content) VALUES (%s,%s,'assistant',%s)",
          (user_id, sid, reply), fetch=False)
    except:
        pass

    return jsonify({"reply": reply, "session_id": sid})

# ---------------------------------------------------------------------------
# API Routes — AI Recommendations
# ---------------------------------------------------------------------------
@app.route("/api/recommend", methods=["POST"])
def api_recommend():
    d = request.json or {}
    pet_info = d.get("pet", {})
    symptoms = d.get("symptoms", "")
    if not symptoms:
        return jsonify({"error": "Please describe symptoms or needs"}), 400

    recs = ai_product_recommendation(pet_info, symptoms)
    results = []
    for rec in recs[:3]:
        product = q1("SELECT p.*, c.name as category_name FROM products p JOIN categories c ON p.category_id=c.id WHERE p.id=%s",
                      (rec.get("product_id"),))
        if product:
            results.append({"product": dict(product), "reason": rec.get("reason", "")})

    return jsonify({"recommendations": results})

# ---------------------------------------------------------------------------
# API Routes — Pets
# ---------------------------------------------------------------------------
@app.route("/api/pets", methods=["GET"])
@login_required
def api_pets():
    pets = q("SELECT * FROM pets WHERE user_id=%s ORDER BY name", (session["user_id"],))
    return jsonify({"pets": [dict(p) for p in (pets or [])]})

@app.route("/api/pets", methods=["POST"])
@login_required
def api_add_pet():
    d = request.json or {}
    pet = q1("""INSERT INTO pets (user_id,name,species,breed,weight_lbs,age_years,conditions)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (session["user_id"], d.get("name",""), d.get("species","dog"), d.get("breed",""),
         d.get("weight_lbs"), d.get("age_years"), d.get("conditions","")))
    return jsonify({"pet": dict(pet)})

# ---------------------------------------------------------------------------
# API Routes — Orders
# ---------------------------------------------------------------------------
@app.route("/api/orders", methods=["POST"])
@login_required
def api_create_order():
    d = request.json or {}
    items = d.get("items", [])
    if not items:
        return jsonify({"error": "Cart is empty"}), 400

    subtotal = 0
    order_items = []
    for item in items:
        product = q1("SELECT id,name,price_cents,requires_rx FROM products WHERE id=%s", (item.get("product_id"),))
        if not product:
            continue
        qty = max(1, int(item.get("quantity", 1)))
        line_total = product["price_cents"] * qty
        subtotal += line_total
        order_items.append({
            "product_id": product["id"],
            "name": product["name"],
            "quantity": qty,
            "price_cents": product["price_cents"],
            "line_total": line_total,
            "requires_rx": product["requires_rx"]
        })

    tax = int(subtotal * 0.08)
    shipping = 0 if subtotal >= 4999 else 599
    total = subtotal + tax + shipping

    order = q1("""INSERT INTO orders (user_id,items,subtotal_cents,tax_cents,shipping_cents,total_cents,shipping_address)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (session["user_id"], json.dumps(order_items), subtotal, tax, shipping, total,
         json.dumps(d.get("shipping_address", {}))))

    return jsonify({"order": dict(order)})

@app.route("/api/orders", methods=["GET"])
@login_required
def api_get_orders():
    orders = q("SELECT * FROM orders WHERE user_id=%s ORDER BY created_at DESC", (session["user_id"],))
    return jsonify({"orders": [dict(o) for o in (orders or [])]})

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": SITE_NAME})

# ---------------------------------------------------------------------------
# Serve SPA
# ---------------------------------------------------------------------------
_FRONTEND_V2 = Path(__file__).parent / "static" / "index-v2.html"
_FRONTEND_V1 = Path(__file__).parent / "static" / "index.html"
FRONTEND_PATH = _FRONTEND_V2 if _FRONTEND_V2.exists() else _FRONTEND_V1
FRONTEND_HTML = ""
if FRONTEND_PATH.exists():
    FRONTEND_HTML = FRONTEND_PATH.read_text()

@app.route("/")
def index():
    return FRONTEND_HTML or "<h1>Crittr</h1><p>Frontend loading...</p>"

@app.route("/<path:path>")
def catch_all(path):
    static_path = Path(__file__).parent / "static" / path
    if static_path.exists() and static_path.is_file():
        return send_from_directory("static", path)
    return FRONTEND_HTML or "<h1>Crittr</h1><p>Frontend loading...</p>"

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
with app.app_context():
    try:
        init_db()
    except Exception as e:
        print(f"Warning: Database initialization failed: {e}")
        print("App will continue without database.")

register_stripe_routes(app, q=q, q1=q1, login_required=login_required, get_db=get_db)

# Phase 3 - Pet profile + pet-scoped chat (Voss-enhanced)
# (register_pets_routes internally calls ensure_pets_schema after wiring _q = q,
# so we don't invoke it directly here — doing so before the register call leaves
# the pets_routes module-level _q helper as None and the schema call no-ops.)
register_pets_routes(app, q=q, q1=q1, login_required=login_required, get_db=get_db)

# CRITTR CHANNEL - YouTube integration blueprint
if youtube_bp is not None:
    app.register_blueprint(youtube_bp)

# ---------------------------------------------------------------------------
# Phase 6 — AI operations automation
# ---------------------------------------------------------------------------
try:
    register_admin_dashboard(app, q)
except Exception as _e:
    print(f"Warning: register_admin_dashboard failed: {_e}")

try:
    register_event_routes(app, q)
except Exception as _e:
    print(f"Warning: register_event_routes failed: {_e}")

# Wire LLM fallback observer so alerts.py can record anthropic->openai fallovers
try:
    set_fallback_observer(
        lambda provider, stage, err: record_fallback(q, provider, stage, err)
    )
except Exception as _e:
    print(f"Warning: set_fallback_observer failed: {_e}")

# ---------------------------------------------------------------------------
# Phase 7 — content, viral, regional
# ---------------------------------------------------------------------------
# Region middleware first — later handlers rely on g.region / g.region_config
try:
    register_region_middleware(app)
except Exception as _e:
    print(f"Warning: register_region_middleware failed: {_e}")

try:
    register_seo_landings(app)
except Exception as _e:
    print(f"Warning: register_seo_landings failed: {_e}")

try:
    register_find_vet_routes(app)
except Exception as _e:
    print(f"Warning: register_find_vet_routes failed: {_e}")

try:
    register_referral_routes(app, q, require_login=login_required)
except Exception as _e:
    print(f"Warning: register_referral_routes failed: {_e}")

# Phase A.2 — MEDVi-style category pages (/shop/<slug>)
try:
    register_shop_routes(app, q=q)
except Exception as _e:
    print(f"Warning: register_shop_routes failed: {_e}")

# Phase B.6 — seed static SVG tiles into products.image_url (idempotent)
try:
    ensure_product_images(q)
except Exception as _e:
    print(f"Warning: ensure_product_images failed: {_e}")

# Phase B.7 — rebrand the 4 Rx products to crittr generics (idempotent)
try:
    ensure_rx_rebrand(q)
    register_rx_rebrand_redirects(app)
except Exception as _e:
    print(f"Warning: ensure_rx_rebrand failed: {_e}")

# Phase B.10 — dynamic OG image generation at /og/<slug>.png
try:
    register_og_routes(app)
except Exception as _e:
    print(f"Warning: register_og_routes failed: {_e}")

# Phase C.1 — legal/policy pages required for Stripe live-mode review
try:
    register_legal_routes(app)
except Exception as _e:
    print(f"Warning: register_legal_routes failed: {_e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

# --- Phase 6+7 periodic jobs (APScheduler, in-process) ---
try:
    from scheduler import start_scheduler
    start_scheduler()
except Exception as _sched_e:
    print(f"Warning: scheduler failed to start: {_sched_e}")
