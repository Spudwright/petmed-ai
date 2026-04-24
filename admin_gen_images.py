"""crittr.ai — admin endpoint that generates AI product images via a
detached subprocess so work continues after the HTTP request returns.

Design:
  /admin/gen-product-images?token=...&pat=...&slug=<all|specific>
  -> validates, spawns a detached Python process (start_new_session=True),
     returns 202 immediately. Child process generates images and commits
     them to GitHub independently of gunicorn's worker lifecycle.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import time
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


def _run_batch_in_child(pat: str, slug_filter: str, quality: str) -> None:
    """Entrypoint used by the detached child process."""
    from openai import OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        print("ERR no OPENAI_API_KEY in env", flush=True)
        return
    client = OpenAI(api_key=openai_key)

    if slug_filter and slug_filter != "all":
        slugs = [slug_filter]
    else:
        slugs = list(PRODUCT_PROMPTS.keys())

    for i, slug in enumerate(slugs, 1):
        print(f"[child] {i}/{len(slugs)} {slug}", flush=True)
        try:
            png = _generate_one(client, slug, quality)
            _github_put_file(
                pat,
                f"static/products/{slug}.png",
                png,
                f"Add AI-generated product photo for {slug}",
            )
            print(f"[child] OK {slug} ({len(png)} bytes)", flush=True)
        except Exception as e:
            print(f"[child] ERR {slug}: {e}", flush=True)
        if i < len(slugs):
            time.sleep(2)
    print("[child] done", flush=True)


def register_admin_gen_images(app):
    @app.route("/admin/gen-product-images")
    def gen_product_images():
        admin_token = request.args.get("token", "")
        pat         = request.args.get("pat", "")
        slug        = request.args.get("slug", "all").strip() or "all"
        quality     = request.args.get("quality", "standard")

        expected_token = os.environ.get("ADMIN_TOKEN") or "crittr-gen-2026"
        if admin_token != expected_token:
            return jsonify({"error": "unauthorized"}), 403
        if not pat or not pat.startswith("github_pat_"):
            return jsonify({"error": "missing ?pat=github_pat_..."}), 400
        if slug != "all" and slug not in PRODUCT_PROMPTS:
            return jsonify({"error": f"unknown slug: {slug}"}), 400
        if not os.environ.get("OPENAI_API_KEY"):
            return jsonify({"error": "OPENAI_API_KEY not set"}), 500

        # Spawn a detached child process that will survive gunicorn worker
        # recycling and HTTP request termination.
        script = (
            "import sys, os; "
            "sys.path.insert(0, os.getcwd()); "
            "from admin_gen_images import _run_batch_in_child; "
            f"_run_batch_in_child({pat!r}, {slug!r}, {quality!r})"
        )
        try:
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except Exception as e:
            return jsonify({"error": f"spawn: {type(e).__name__}: {str(e)[:300]}"}), 500

        slugs = [slug] if slug != "all" else list(PRODUCT_PROMPTS.keys())
        return jsonify({
            "ok": True,
            "message": "spawned detached child process",
            "pid_approx": "unknown",
            "total": len(slugs),
            "check": "watch GitHub commits land on main over ~10 minutes",
        }), 202
