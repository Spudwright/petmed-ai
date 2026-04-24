"""crittr.ai — one-shot admin endpoint that generates AI product images
server-side and commits them to GitHub. Removes the need for a local
terminal.

Endpoint
--------
GET /admin/gen-product-images?token=<ADMIN_TOKEN>&pat=<GITHUB_PAT>
    Optional:
        slug=<single-slug>   to regenerate just one
        quality=standard|hd  default standard

Uses Railway's OPENAI_API_KEY to hit OpenAI's image API (dall-e-3), then
uploads each resulting PNG to the GitHub repo via the Contents API using
the PAT passed in the URL. Triggers a Railway redeploy naturally.

Once all images are committed, hit /admin/finish-product-images to
flip product_images.py to the new /static/products/<slug>.png paths
(that's a separate commit and is done by claude-code, not this route).

Remove this module and its import after the one-time use.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Dict

from flask import Response, jsonify, request

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
    """Call OpenAI's image API, fetch the resulting PNG, return the bytes."""
    import urllib.request
    prompt = STYLE_PRELUDE + PRODUCT_PROMPTS[slug]
    resp = openai_client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        quality=quality,
        n=1,
        response_format="url",
    )
    img_url = resp.data[0].url
    with urllib.request.urlopen(img_url, timeout=60) as r:
        return r.read()


def _github_put_file(pat: str, repo_path: str, content_bytes: bytes, message: str) -> dict:
    """Create or update a file in the repo via GitHub Contents API.

    If the file exists we need to supply its SHA to update it.
    """
    import urllib.request, urllib.error
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "crittr-admin",
        "Content-Type": "application/json",
    }
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{repo_path}"

    # Check if file exists to get its SHA
    sha = None
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
            sha = data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    body = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
    }
    if sha:
        body["sha"] = sha

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def register_admin_gen_images(app):
    @app.route("/admin/gen-product-images")
    def gen_product_images():
        admin_token = request.args.get("token", "")
        pat         = request.args.get("pat", "")
        slug_only   = request.args.get("slug", "").strip()
        quality     = request.args.get("quality", "standard")

        expected_token = os.environ.get("ADMIN_TOKEN") or "crittr-gen-2026"
        if admin_token != expected_token:
            return jsonify({"error": "unauthorized"}), 403
        if not pat or not pat.startswith("github_pat_"):
            return jsonify({"error": "missing ?pat=github_pat_..."}), 400

        openai_key = os.environ.get("OPENAI_API_KEY")
        if not openai_key:
            return jsonify({"error": "OPENAI_API_KEY not set on Railway"}), 500

        try:
            from openai import OpenAI
        except ImportError:
            return jsonify({"error": "openai package not installed"}), 500

        client = OpenAI(api_key=openai_key)

        slugs = [slug_only] if slug_only else list(PRODUCT_PROMPTS.keys())
        if slug_only and slug_only not in PRODUCT_PROMPTS:
            return jsonify({"error": f"unknown slug: {slug_only}"}), 400

        report = []
        for i, slug in enumerate(slugs, 1):
            try:
                log.info(f"[gen] {i}/{len(slugs)} {slug}...")
                png_bytes = _generate_one(client, slug, quality)
                gh_result = _github_put_file(
                    pat,
                    f"static/products/{slug}.png",
                    png_bytes,
                    f"Add AI-generated product photo for {slug}",
                )
                report.append({
                    "slug": slug,
                    "status": "ok",
                    "bytes": len(png_bytes),
                    "commit": gh_result.get("commit", {}).get("sha", "")[:12],
                })
            except Exception as e:
                report.append({"slug": slug, "status": "err", "error": str(e)[:300]})
            # Modest gap so we don't hammer the API
            if i < len(slugs):
                time.sleep(1.2)

        return jsonify({
            "ok": all(r["status"] == "ok" for r in report),
            "count": len(report),
            "report": report,
        })
