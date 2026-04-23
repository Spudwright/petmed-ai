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


# slug -> {"amazon_search": "<query>"} or {"amazon_asin": "BXXXXXXXXX"}
# Prefer ASIN when you know it (deep-link to specific product) — falls back
# to search URL which always works.
_AFFILIATE_MAP = {
    "frontline-gold":      {"amazon_search": "Frontline Gold dog flea tick"},
    "seresto-collar":      {"amazon_search": "Seresto flea tick collar dog"},
    "cosequin-ds-msm":     {"amazon_search": "Cosequin DS Plus MSM joint"},
    "dasuquin-advanced":   {"amazon_search": "Dasuquin Advanced joint dog"},
    "adaptil-calm":        {"amazon_search": "Adaptil Calm Diffuser dog"},
    "composure-pro":       {"amazon_search": "VetriScience Composure Pro dog"},
    "fortiflora":          {"amazon_search": "Purina FortiFlora Probiotic"},
    "welactin-omega3":     {"amazon_search": "Welactin Omega-3 dog cat"},
    "greenies-original":   {"amazon_search": "Greenies Original dental treats dog"},
    "oravet-chews":        {"amazon_search": "Oravet Dental Hygiene Chews dog"},
    "pet-tabs-plus":       {"amazon_search": "Pet-Tabs Plus multivitamin dog"},
    "nucat-multivitamin":  {"amazon_search": "VetriScience Nu Cat multivitamin"},
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
    """Add amazon_url + chewy_url cols if they don't exist."""
    try:
        q(
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS amazon_url TEXT",
            fetch=False,
        )
        q(
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS chewy_url TEXT",
            fetch=False,
        )
    except Exception as e:
        log.warning("ensure_affiliate_schema failed: %s", e)


def ensure_affiliate_urls(q) -> None:
    """Seed amazon_url for the 12 OTC products (only where NULL/empty).

    Runs on every startup; the NULL guard means manually-set URLs are
    never overwritten.
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
        except Exception as e:
            log.warning("ensure_affiliate_urls %s: %s", slug, e)
