"""crittr.ai — Find-a-vet geolocation (Phase 7.4).

Public API
----------
    register_find_vet_routes(app) -> None
        Wires POST /api/vets/nearby.

Flow
----
The frontend grabs the user's lat/long via navigator.geolocation after
a VET TOMORROW verdict, then POSTs {lat, lng, radius_km?} to this
endpoint. We call the Google Places Nearby Search API for the top 5
veterinary_care results within 10km and return a JSON array of:

    {name, address, distance_km, rating, open_now, url}

The front-end renders a card under the partner CTA so owners have an
in-person option alongside the teletriage option.

Env
---
    GOOGLE_PLACES_API_KEY    required
    FIND_VET_CACHE_TTL       optional (seconds, default 600)
"""
import os
import json
import time
import math
import logging
from urllib import request as urllib_request, parse as urllib_parse
from urllib.error import URLError
from flask import request, jsonify

log = logging.getLogger("crittr.find_vet")

try:
    _CACHE_TTL = int(os.environ.get("FIND_VET_CACHE_TTL", "600"))
except ValueError:
    _CACHE_TTL = 600

_PLACES_URL = (
    "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
)

# In-process cache: (lat_round, lng_round, radius) -> (expires, payload)
_cache = {}


def _haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 2)


def _cache_key(lat, lng, radius_m):
    return (round(lat, 3), round(lng, 3), radius_m)


def _fetch_nearby(lat, lng, radius_m, api_key):
    params = urllib_parse.urlencode({
        "location": f"{lat},{lng}",
        "radius": radius_m,
        "type": "veterinary_care",
        "key": api_key,
    })
    url = f"{_PLACES_URL}?{params}"
    try:
        with urllib_request.urlopen(url, timeout=4) as r:
            body = r.read().decode("utf-8")
            return json.loads(body)
    except URLError as e:
        log.warning("places fetch failed: %s", e)
        return None
    except Exception as e:
        log.warning("places parse failed: %s", e)
        return None


def find_nearby(lat, lng, radius_km=10, limit=5):
    """Returns list of dicts: {name, address, distance_km, rating, open_now, url}.
    Empty list on error / no API key."""
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
        return []
    radius_m = int(max(1, min(radius_km, 50)) * 1000)
    key = _cache_key(lat, lng, radius_m)
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        data = hit[1]
    else:
        data = _fetch_nearby(lat, lng, radius_m, api_key)
        if data is None:
            return []
        _cache[key] = (now + _CACHE_TTL, data)

    results = data.get("results") or []
    out = []
    for r in results:
        loc = ((r.get("geometry") or {}).get("location") or {})
        rlat = loc.get("lat")
        rlng = loc.get("lng")
        if rlat is None or rlng is None:
            continue
        place_id = r.get("place_id")
        gmap_url = (
            f"https://www.google.com/maps/place/?q=place_id:{place_id}"
            if place_id else None
        )
        out.append({
            "name": r.get("name"),
            "address": r.get("vicinity") or r.get("formatted_address"),
            "distance_km": _haversine_km(lat, lng, rlat, rlng),
            "rating": r.get("rating"),
            "open_now": ((r.get("opening_hours") or {}).get("open_now")),
            "url": gmap_url,
        })
    out.sort(key=lambda x: x["distance_km"])
    return out[:limit]


# ---------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------
def register_find_vet_routes(app):
    @app.route("/api/vets/nearby", methods=["POST"])
    def api_vets_nearby():
        data = request.get_json(silent=True) or {}
        try:
            lat = float(data.get("lat"))
            lng = float(data.get("lng"))
        except (TypeError, ValueError):
            return jsonify({"error": "lat/lng required as numbers"}), 400
        try:
            radius_km = float(data.get("radius_km") or 10)
        except (TypeError, ValueError):
            radius_km = 10
        # Sanity-check the coordinates
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return jsonify({"error": "invalid coordinates"}), 400
        vets = find_nearby(lat, lng, radius_km=radius_km, limit=5)
        return jsonify({"vets": vets, "count": len(vets)})
