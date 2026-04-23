"""crittr.ai — Amazon/Chewy affiliate layer (Lever 1 monetization).

Adds amazon_url / chewy_url columns to the products table and seeds them
with affiliate URLs for the 12 OTC products.  The 4 crittr-branded Rx
products (crittr monthly heartworm chew, etc.) never get affiliate URLs
because those are our own private-label — handled by cart + compounding
partner when that's wired up.

Uses Amazon search URLs by default so this ships TODAY without needing
specific ASINs.  Swap to direct ASIN URLs by editing _AFFILIATE_MAP;
the amazon_url column gets re-seeded whenever the value is NULL/empty,
so existing hand-set URLs aren't clobbered.

Env
---
    AMAZON_ASSOCIATES_TAG   e.g. "crittrai-20". If unset, uses a
                            placeholder so the feature works during
                            dev but drops no affiliate revenue until
                            the real tag is set.

Public API
----------
    ensure_affiliate_schema(q)
    ensure_affiliate_urls(q)
"""
from __future__ import annotations

import logging
import os
import urllib.parse

log = logging.getLogger("crittr.affiliate")


# slug -> { amazon_search | amazon_asin, public_name, public_blurb }
#   public_name: customer-facing generic label (no manufacturer brand)
#   public_blurb: one-line "why crittr recommends this" description
# The underlying retail product is surfaced only on Amazon, which is
# where brand transparency correctly happens per Associates program rules.
_AFFILIATE_MAP = {
    "frontline-gold": {
        "amazon_search": "Frontline Gold dog flea tick",
        "public_name":  "Monthly flea & tick topical",
        "public_blurb": "Topical drops behind the shoulder blades, once a month. Good first-line choice for dogs and cats with active flea or tick exposure.",
    },
    "seresto-collar": {
        "amazon_search": "Seresto flea tick collar dog",
        "public_name":  "8-month flea & tick collar",
        "public_blurb": "Slow-release collar that keeps working for up to 8 months — set it and forget it. Our vet advisors' pick for low-maintenance prevention.",
    },
    "cosequin-ds-msm": {
        "amazon_search": "Cosequin DS Plus MSM joint",
        "public_name":  "Daily joint & mobility chew",
        "public_blurb": "Glucosamine + chondroitin + MSM chew for dogs slowing down on walks or stairs. The supplement most orthopedic vets start with.",
    },
    "dasuquin-advanced": {
        "amazon_search": "Dasuquin Advanced joint dog",
        "public_name":  "Advanced joint support chew",
        "public_blurb": "Stepped-up joint formula for dogs already on a basic supplement — adds avocado/soy unsaponifiables and boswellia.",
    },
    "adaptil-calm": {
        "amazon_search": "Adaptil Calm Diffuser dog",
        "public_name":  "Calming pheromone diffuser",
        "public_blurb": "Plug-in that releases a dog-appeasing pheromone. Helps with separation, storms, and new-home anxiety. Drug-free.",
    },
    "composure-pro": {
        "amazon_search": "VetriScience Composure Pro dog",
        "public_name":  "Clinical-strength calming chew",
        "public_blurb": "L-theanine + colostrum chew for fast-acting situational calm — thunderstorms, vet visits, fireworks. For dogs and cats.",
    },
    "fortiflora": {
        "amazon_search": "Purina FortiFlora Probiotic",
        "public_name":  "Daily probiotic powder",
        "public_blurb": "Sprinkle-on-food probiotic for GI upset or post-antibiotic recovery. Safe for dogs and cats of any age.",
    },
    "welactin-omega3": {
        "amazon_search": "Welactin Omega-3 dog cat",
        "public_name":  "Omega-3 skin & coat liquid",
        "public_blurb": "Cold-water-fish omega-3 oil for itchy skin, dull coat, and inflammation. Pump onto food daily.",
    },
    "greenies-original": {
        "amazon_search": "Greenies Original dental treats dog",
        "public_name":  "Daily dental chew",
        "public_blurb": "Once-a-day dental treat that mechanically cleans teeth and freshens breath. Good for dogs that won't tolerate brushing.",
    },
    "oravet-chews": {
        "amazon_search": "Oravet Dental Hygiene Chews dog",
        "public_name":  "Prescription-strength dental chew",
        "public_blurb": "Clinical-grade dental chew that coats the teeth to block plaque formation. A step up from standard dental treats.",
    },
    "pet-tabs-plus": {
        "amazon_search": "Pet-Tabs Plus multivitamin dog",
        "public_name":  "Daily multivitamin for dogs",
        "public_blurb": "Complete daily multivitamin — safety net for home-cooked diets, seniors, or picky eaters.",
    },
    "nucat-multivitamin": {
        "amazon_search": "VetriScience Nu Cat multivitamin",
        "public_name":  "Daily multivitamin chew for cats",
        "public_blurb": "Soft chew multivitamin for cats — covers the usual gaps in commercial feline diets (taurine, B-vitamins, antioxidants).",
    },
}


def _tag() -> str:
    """Return the Amazon Associates tag from env, with a safe fallback."""
    return os.environ.get("AMAZON_ASSOCIATES_TAG", "crittrai-20")


def _build_amazon_url(entry: dict) -> str:
    tag = _tag()
    if "amazon_asin" in entry:
        return f"https://www.amazon.com/dp/{entry['amazon_asin']}?tag={tag}"
    q = urllib.parse.quote_plus(entry["amazon_search"])
    return f"https://www.amazon.com/s?k={q}&tag={tag}"


def ensure_affiliate_schema(q) -> None:
    """Add amazon_url / chewy_url / public_name / public_blurb cols."""
    cols = (
        "amazon_url TEXT",
        "chewy_url TEXT",
        "public_name TEXT",
        "public_blurb TEXT",
    )
    for col in cols:
        try:
            q(f"ALTER TABLE products ADD COLUMN IF NOT EXISTS {col}", fetch=False)
        except Exception as e:
            log.warning("ensure_affiliate_schema %s failed: %s", col, e)


def ensure_affiliate_urls(q) -> None:
    """Seed amazon_url + public_name + public_blurb for the 12 OTC products.

    Runs on every startup.  Each column has its own NULL guard so
    manually-set values are never overwritten.
    """
    ensure_affiliate_schema(q)
    for slug, entry in _AFFILIATE_MAP.items():
        try:
            url = _build_amazon_url(entry)
            q(
                "UPDATE products SET amazon_url=%s "
                "WHERE slug=%s AND (amazon_url IS NULL OR amazon_url = '')",
                (url, slug),
                fetch=False,
            )
            if "public_name" in entry:
                q(
                    "UPDATE products SET public_name=%s "
                    "WHERE slug=%s AND (public_name IS NULL OR public_name = '')",
                    (entry["public_name"], slug),
                    fetch=False,
                )
            if "public_blurb" in entry:
                q(
                    "UPDATE products SET public_blurb=%s "
                    "WHERE slug=%s AND (public_blurb IS NULL OR public_blurb = '')",
                    (entry["public_blurb"], slug),
                    fetch=False,
                )
        except Exception as e:
            log.warning("ensure_affiliate_urls %s: %s", slug, e)
