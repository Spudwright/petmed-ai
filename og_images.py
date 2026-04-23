"""crittr.ai — dynamic OG image generation.

Renders 1200x630 PNG social cards on-demand for:
  /og/cover.png              -> homepage default
  /og/shop-<slug>.png        -> shop categories (dogs/cats/supplements/rx)
  /og/c-<slug>.png           -> SEO landing pages
  /og/<slug>.png             -> fallback -> routes to the right template based on slug prefix

Cards are rendered fresh in-memory; Pillow is lightweight and each render
takes ~40ms.  Results are cached in a simple in-process dict so repeat hits
are fast, and served with long Cache-Control headers so CDNs store them too.

Public API
----------
    register_og_routes(app)
"""
from __future__ import annotations

import io
import logging
import textwrap
from typing import Tuple

from flask import Response, abort

log = logging.getLogger("crittr.og")

# ── Lazy Pillow import so the app still boots if the dep is missing. ──
try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except Exception as _e:
    log.warning("og_images: Pillow unavailable: %s", _e)
    _HAS_PIL = False


# Brand palette (keeps in sync with CSS vars)
CREAM   = (253, 251, 245)
CREAM_2 = (246, 241, 231)
SAGE50  = (242, 247, 241)
SAGE100 = (228, 239, 226)
SAGE300 = (166, 201, 162)
SAGE500 = (107, 158, 107)
SAGE600 = (82, 126, 84)
SAGE700 = (62, 99, 64)
SAGE800 = (45, 74, 48)
SAGE900 = (31, 50, 33)
INK     = (28, 42, 31)
MUTED   = (110, 125, 112)
TERRA   = (212, 149, 106)


def _load_font(preferred_names, size):
    for name in preferred_names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fonts():
    serif = _load_font([
        "DejaVuSerif-Bold.ttf",
        "DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    ], 82)
    serif_small = _load_font([
        "DejaVuSerif-Bold.ttf",
        "DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    ], 40)
    sans = _load_font([
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ], 30)
    sans_bold = _load_font([
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ], 26)
    return serif, serif_small, sans, sans_bold


def _wrap(text, font, max_w):
    """Greedy word-wrap given a Pillow font and pixel width budget."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        # Use textbbox so we work on both new and older Pillow.
        try:
            bbox = font.getbbox(test)
            width = bbox[2] - bbox[0]
        except Exception:
            width = font.getsize(test)[0]
        if width <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _logo_dot(draw, x, y, r=10):
    # halo
    draw.ellipse((x - r - 6, y - r - 6, x + r + 6, y + r + 6), fill=SAGE100)
    draw.ellipse((x - r, y - r, x + r, y + r), fill=SAGE500)


def _base_canvas():
    img = Image.new("RGB", (1200, 630), CREAM)
    draw = ImageDraw.Draw(img)
    # Soft background orbs in brand colors
    draw.ellipse((780, -220, 1400, 400), fill=SAGE50)
    draw.ellipse((-300, 380, 340, 900),  fill=CREAM_2)
    return img, draw


def render_og(eyebrow: str, title: str, footer: str = "crittr.ai") -> bytes:
    """Return PNG bytes for a branded social card."""
    if not _HAS_PIL:
        return b""

    img, draw = _base_canvas()
    serif, serif_small, sans, sans_bold = _fonts()

    # Top-left logo
    _logo_dot(draw, 88, 96, r=11)
    draw.text((114, 68), "crittr", font=serif_small, fill=SAGE800)

    # Eyebrow (small uppercase tag)
    if eyebrow:
        draw.text((80, 200), eyebrow.upper(), font=sans_bold, fill=SAGE600)

    # Headline wrapped to ~960px width
    lines = _wrap(title, serif, 1040)
    y = 260
    for line in lines[:3]:
        draw.text((80, y), line, font=serif, fill=INK)
        y += 96
        if y > 480:
            break

    # Sage underline accent
    draw.rectangle((80, 542, 240, 550), fill=SAGE500)

    # Footer domain
    draw.text((80, 562), footer, font=sans, fill=MUTED)

    # Save to bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Cache so repeat hits within a worker are instant ──
_CACHE: dict[str, bytes] = {}


def _get(key: str, eyebrow: str, title: str, footer: str = "crittr.ai") -> bytes:
    if key not in _CACHE:
        _CACHE[key] = render_og(eyebrow, title, footer)
    return _CACHE[key]


# Precomputed spec lookup: map slug -> (eyebrow, title)
_SHOP = {
    "dogs":        ("Curated for dogs",     "The food, meds and routines dogs actually need."),
    "cats":        ("Curated for cats",     "Cats aren't small dogs. Care built for feline physiology."),
    "supplements": ("Daily wellness",       "Joint, gut, skin, calm — supplements vets actually recommend."),
    "rx":          ("Prescription",         "Prescriptions that start with a consult, not a form."),
}


def _respond_png(png: bytes) -> Response:
    if not png:
        abort(503)
    resp = Response(png, mimetype="image/png")
    # Long cache: OG images don't change often; bust via slug change.
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


def register_og_routes(app):
    """Wire /og/<slug>.png endpoints into the Flask app."""

    @app.route("/og/cover.png")
    def og_cover():
        png = _get(
            "cover",
            "Built by vets · free to start",
            "It's 2am and the vet is closed. Is this an ER, or can it wait?",
        )
        return _respond_png(png)

    @app.route("/og/shop-<slug>.png")
    def og_shop(slug):
        spec = _SHOP.get(slug)
        if not spec:
            abort(404)
        eyebrow, title = spec
        png = _get(f"shop-{slug}", eyebrow, title)
        return _respond_png(png)

    @app.route("/og/c-<slug>.png")
    def og_c(slug):
        # Look up topic; fall back to generic if not found so link previews never hard-break.
        try:
            from seo_landings import TOPICS
            topic = TOPICS.get(slug)
        except Exception:
            topic = None
        if topic:
            eyebrow = f"Tends to be: {topic.lean}"
            title = topic.title
        else:
            eyebrow = "Pet healthcare"
            title = "crittr — triage, vets, and meds for the 2am moments."
        png = _get(f"c-{slug}", eyebrow, title)
        return _respond_png(png)

    @app.route("/og/default.png")
    def og_default():
        # Alias for compatibility with any stale references
        return og_cover()
