"""crittr.ai — Regional configuration (Phase 7.7).

Country-specific swaps for poison hotline, partner set, currency,
and legal disclosure text. The product isn't actively marketed in
non-US regions yet — this module just keeps crittr *safe* to use
when a UK or AU visitor lands on it.

Public API
----------
    get_region_config(region_code) -> dict
    infer_region_from_request() -> str
        Accepts "US" / "UK" / "AU" / "CA" / "FALLBACK".
    render_region_footer(region_code) -> str  (small HTML snippet)
    register_region_middleware(app) -> None
        Attaches g.region to each request.
"""
import logging
from flask import request, g

log = logging.getLogger("crittr.regions")


# ---------------------------------------------------------------
# Region catalog
# ---------------------------------------------------------------
_REGIONS = {
    "US": {
        "country_name": "United States",
        "currency": "USD",
        "currency_symbol": "$",
        "poison_control": {
            "name": "ASPCA Animal Poison Control",
            "phone": "(888) 426-4435",
            "url": "https://www.aspca.org/pet-care/animal-poison-control",
            "hours": "24/7",
            "fee_hint": "$95 consult fee",
        },
        "teletriage_partner_slugs": ["vetster", "airvet"],
        "pharmacy_partner_slugs": ["chewy_pharmacy"],
        "legal_disclosure": (
            "crittr.ai provides informational triage support and does not "
            "replace a licensed veterinarian. If you believe your pet is "
            "experiencing an emergency, contact your local emergency "
            "veterinary hospital immediately."
        ),
    },
    "CA": {
        "country_name": "Canada",
        "currency": "CAD",
        "currency_symbol": "$",
        "poison_control": {
            "name": "Pet Poison Helpline (serves Canada)",
            "phone": "(855) 764-7661",
            "url": "https://www.petpoisonhelpline.com/",
            "hours": "24/7",
            "fee_hint": "USD $85 consult fee",
        },
        "teletriage_partner_slugs": ["vetster"],
        "pharmacy_partner_slugs": [],
        "legal_disclosure": (
            "crittr.ai provides informational triage support and does not "
            "replace a licensed Canadian veterinarian. If you believe "
            "your pet is experiencing an emergency, contact your local "
            "emergency veterinary hospital immediately."
        ),
    },
    "UK": {
        "country_name": "United Kingdom",
        "currency": "GBP",
        "currency_symbol": "£",
        "poison_control": {
            "name": "Animal PoisonLine (VPIS)",
            "phone": "01202 509000",
            "url": "https://www.animalpoisonline.co.uk/",
            "hours": "24/7",
            "fee_hint": "£35–45 per call",
        },
        "teletriage_partner_slugs": ["vetster"],  # VetConnect placeholder
        "pharmacy_partner_slugs": [],
        "legal_disclosure": (
            "crittr.ai provides informational triage support and does not "
            "replace a RCVS-registered veterinary surgeon. Emergency "
            "concerns should go to your nearest out-of-hours veterinary "
            "practice."
        ),
    },
    "AU": {
        "country_name": "Australia",
        "currency": "AUD",
        "currency_symbol": "$",
        "poison_control": {
            # There is no single 24/7 hotline; we direct to the nearest
            # emergency vet. AAPCC placeholder.
            "name": "Australian Animal Poisons Helpline",
            "phone": "1300 869 738",
            "url": "https://www.animalpoisons.com.au/",
            "hours": "9am–11pm AEST",
            "fee_hint": "AUD $79 per consultation",
        },
        "teletriage_partner_slugs": [],  # PetsApp placeholder
        "pharmacy_partner_slugs": [],
        "legal_disclosure": (
            "crittr.ai provides informational triage support and does not "
            "replace an AVA-registered veterinarian. Emergencies should "
            "go to your nearest emergency veterinary hospital immediately."
        ),
    },
    "FALLBACK": {
        "country_name": None,
        "currency": "USD",
        "currency_symbol": "$",
        "poison_control": {
            "name": "Your nearest emergency veterinary hospital",
            "phone": None,
            "url": None,
            "hours": None,
            "fee_hint": None,
        },
        "teletriage_partner_slugs": ["vetster"],
        "pharmacy_partner_slugs": [],
        "legal_disclosure": (
            "crittr.ai provides informational triage support and does not "
            "replace a licensed veterinarian in your jurisdiction. In a "
            "true emergency, go to your nearest emergency veterinary "
            "hospital."
        ),
    },
}


def get_region_config(region_code):
    """Return the full config dict for a region, falling back silently."""
    code = (region_code or "").strip().upper()
    return _REGIONS.get(code) or _REGIONS["FALLBACK"]


# ---------------------------------------------------------------
# Region inference
# ---------------------------------------------------------------
# Priority:
#   1. Explicit `?region=UK` query string (for testing)
#   2. Cookie `crittr_region`
#   3. CF-IPCountry / X-Country-Code header (CDN-supplied)
#   4. Accept-Language (crude: gb/en-gb -> UK; en-au -> AU; etc.)
#   5. FALLBACK
_LANG_MAP = {
    "en-us": "US", "en_us": "US",
    "en-gb": "UK", "en_gb": "UK",
    "en-ca": "CA", "en_ca": "CA",
    "en-au": "AU", "en_au": "AU",
    "en-nz": "AU",  # NZ gets AU config until we have our own
}


def infer_region_from_request():
    try:
        q = (request.args.get("region") or "").strip().upper()
        if q in _REGIONS:
            return q
        c = (request.cookies.get("crittr_region") or "").strip().upper()
        if c in _REGIONS:
            return c
        for h in ("CF-IPCountry", "X-Country-Code", "X-Vercel-IP-Country"):
            hv = (request.headers.get(h) or "").strip().upper()
            if hv == "GB":
                return "UK"
            if hv in _REGIONS:
                return hv
        al = (request.headers.get("Accept-Language") or "").lower().split(",")[0].strip()
        if al in _LANG_MAP:
            return _LANG_MAP[al]
        if al.startswith("en-"):
            base = al.split(";")[0]
            return _LANG_MAP.get(base, "US")
    except Exception as e:
        log.debug("[regions] infer failed: %s", e)
    return "US"


# ---------------------------------------------------------------
# Flask integration
# ---------------------------------------------------------------
def register_region_middleware(app):
    """Attach g.region = inferred region code on every request."""
    @app.before_request
    def _attach_region():
        try:
            g.region = infer_region_from_request()
            g.region_config = get_region_config(g.region)
        except Exception as e:
            log.debug("[regions] middleware: %s", e)
            g.region = "US"
            g.region_config = _REGIONS["US"]


def render_region_footer(region_code):
    """Return a small HTML snippet safe to drop into the page footer."""
    cfg = get_region_config(region_code)
    pc = cfg["poison_control"]
    parts = [cfg["legal_disclosure"]]
    if pc.get("phone"):
        parts.append(
            f"For suspected poison ingestion: {pc['name']} — "
            f"{pc['phone']} ({pc.get('hours') or 'see site'})."
        )
    return "<p style='font-size:12px;color:#6E7D70;max-width:580px;'>" \
           + " ".join(parts) + "</p>"
