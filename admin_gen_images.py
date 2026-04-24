"""crittr.ai — admin endpoint that generates one product image per request.

Pure synchronous — no threads, no subprocess. Client fetch may time out
at 45s (Chrome CDP) or 60s (browser default) but the WSGI worker keeps
running until it returns. Caller polls GitHub for commit landing.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Dict

from flask import jsonify, request

log = logging.getLogger("crittr.admin_gen_images")


STYLE_PRELUDE = (
    "Minimalist studio product photography. Single object centered on a soft "
    "cream (#FDFBF5) background with a subtle sage-green (#C7DEC4) shadow. "
    "Soft diffused natural lighting, shallow depth of field, photorealistic, "
    "editorial commercial pharmacy/wellness aesthetic. Clean, modern. "
    "No text, no logos, no branding visible unless specified. "
    "Square 1:1 aspect ratio, high resolution. "
)

PRODUCT_PROMPTS: Dict[str, str] = {
    "frontline-gold":            "A small glass pipette applicator with amber-colored liquid inside, single bottle, laying at a slight angle.",
    "seresto-collar":            "A flexible grey-black pet collar coiled in a loose circle, satin finish, no brand tag visible.",
    "cosequin-ds-msm":           "A small pile of dark brown chewable pet supplement tablets, pet-sized, loosely arranged.",
    "dasuquin-advanced":         "Four larger dark brown chewable pet tablets in a loose cluster, shallow focus.",
    "adaptil-calm":              "A small white plug-in diffuser device with a subtle green LED indicator, minimalist, standing upright.",
    "composure-pro":             "A handful of light tan soft-chewable pet treats, small and rounded, arranged informally.",
    "fortiflora":                "A single small white paper sachet/pouch of powder supplement, edge curled slightly.",
    "welactin-omega3":           "An amber glass dropper bottle with golden oil inside, elegant apothecary style.",
    "greenies-original":         "Three green bone-shaped pet dental chews at varied angles, minimalist composition.",
    "oravet-chews":              "A stack of four brown rectangular pet dental chews, product photography.",
    "pet-tabs-plus":             "A small pile of round beige pet supplement tablets, close-up macro shot.",
    "nucat-multivitamin":        "A handful of small pink-beige soft-chewable cat supplements scattered on the surface.",
    "crittr-combo-rx-chew":      "A dark brown hexagonal chewable pet tablet with a small sage-green oval label reading 'crittr' in clean serif font.",
    "crittr-heartworm-chew":     "A heart-shaped dark brown chewable pet tablet with a subtle sage-green 'crittr' wordmark embossed on its surface.",
    "crittr-cat-broad-topical":  "A sage-green glass pipette applicator bottle with a small 'crittr' wordmark in white on the side, single bottle.",
    "crittr-rx-gastro-diet":     "A minimalist cream-colored standing pouch food bag with a small sage-green 'crittr' logo and 'Rx Gastro Diet' label.",
}

GITHUB_REPO = "Spudwright/petmed-ai"
GITHUB_API  = "https://api.github.com"


def _generate_one(openai_client, slug: str, quality: str = "standard") -> bytes:
    import urllib.request
    prompt = STYLE_PRELUDE + PRODUCT_PROMPTS[slug]
    resp = openai_client.images.generate(
        model="dall-e-3", prompt=prompt, size="1024x1024",
        quality=quality, n=1, response_format="url",
    )
    with urllib.request.urlopen(resp.data[0].url, timeout=60) as r:
        return r.read()


def _github_put_file(pat: str, repo_path: str, content_bytes: bytes, message: str) -> dict:
    import urllib.request, urllib.error
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "crittr-admin",
        "Content-Type": "application/json",
    }
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{repo_path}"
    sha = None
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            sha = json.loads(r.read().decode("utf-8")).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    body = {"message": message, "content": base64.b64encode(content_bytes).decode("ascii")}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="PUT")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def register_admin_gen_images(app):
    @app.route("/admin/gen-product-images")
    def gen_product_images():
        admin_token = request.args.get("token", "")
        pat         = request.args.get("pat", "")
        slug        = request.args.get("slug", "").strip()
        quality     = request.args.get("quality", "standard")

        if admin_token != (os.environ.get("ADMIN_TOKEN") or "crittr-gen-2026"):
            return jsonify({"error": "unauthorized"}), 403
        if not pat or not pat.startswith("github_pat_"):
            return jsonify({"error": "missing ?pat=github_pat_..."}), 400
        if not slug or slug not in PRODUCT_PROMPTS:
            return jsonify({"error": f"?slug=<one of {list(PRODUCT_PROMPTS.keys())}>"}), 400

        openai_key = os.environ.get("OPENAI_API_KEY")
        if not openai_key:
            return jsonify({"error": "OPENAI_API_KEY not set"}), 500

        try:
            from openai import OpenAI
        except ImportError:
            return jsonify({"error": "openai SDK missing"}), 500

        client = OpenAI(api_key=openai_key)
        try:
            png = _generate_one(client, slug, quality)
            result = _github_put_file(
                pat, f"static/products/{slug}.png", png,
                f"Add AI-generated product photo for {slug}",
            )
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {str(e)[:300]}"}), 500

        return jsonify({
            "ok": True, "slug": slug, "bytes": len(png),
            "commit": result.get("commit", {}).get("sha", "")[:12],
        })
