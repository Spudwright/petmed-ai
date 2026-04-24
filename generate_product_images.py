"""generate_product_images.py — one-shot AI product image generator.

Generates 16 product photos via OpenAI's image API using your existing
OPENAI_API_KEY, saves them to static/products/<slug>.png, and prints the
git commands to commit them. Built for crittr.ai's Phase E.6 photo
upgrade.

Usage (from the repo root):

    export OPENAI_API_KEY=sk-proj-...
    python generate_product_images.py

Costs at dall-e-3 standard quality, 1024x1024: $0.04 per image x 16 = ~$0.64.
Takes ~3 minutes total (OpenAI rate-limits to ~5 images/minute).

After it finishes:
    git add static/products
    git commit -m "Add AI-generated product photos"
    git push
"""
import os
import sys
import time
from pathlib import Path
import urllib.request

try:
    from openai import OpenAI
except ImportError:
    print("Please install openai first:  pip install openai")
    sys.exit(1)

API_KEY = os.environ.get("OPENAI_API_KEY")
if not API_KEY:
    print("Set OPENAI_API_KEY first:")
    print("  export OPENAI_API_KEY=sk-...")
    sys.exit(1)

client = OpenAI(api_key=API_KEY)
OUT = Path(__file__).parent / "static" / "products"
OUT.mkdir(parents=True, exist_ok=True)

STYLE = (
    "Minimalist studio product photography. Single object centered on a soft "
    "cream (#FDFBF5) background with a subtle sage-green (#C7DEC4) shadow. "
    "Soft diffused natural lighting, shallow depth of field, photorealistic, "
    "editorial commercial pharmacy/wellness aesthetic. Clean, modern. "
    "No text, no logos, no branding visible (unless specified below). "
    "Square 1:1 aspect ratio, high resolution."
)

# Slug -> specific product prompt (no brand names of real competitors).
PRODUCTS = {
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
    # crittr-branded Rx products — DO surface the crittr wordmark on these
    "crittr-combo-rx-chew":      "A dark brown hexagonal chewable pet tablet with a small sage-green oval label reading 'crittr' in clean serif font.",
    "crittr-heartworm-chew":     "A heart-shaped dark brown chewable pet tablet with a subtle sage-green 'crittr' wordmark embossed on its surface.",
    "crittr-cat-broad-topical":  "A sage-green glass pipette applicator bottle with a small 'crittr' wordmark in white on the side, single bottle.",
    "crittr-rx-gastro-diet":     "A minimalist cream-colored standing pouch food bag with a small sage-green 'crittr' logo and 'Rx Gastro Diet' label.",
}


def generate(slug: str, product_prompt: str, quality: str = "standard") -> None:
    dest = OUT / f"{slug}.png"
    if dest.exists():
        print(f"⊙ {slug}.png already exists, skipping (delete to regenerate)")
        return
    prompt = f"{STYLE}\n\n{product_prompt}"
    print(f"→ Generating {slug}...", flush=True)
    resp = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        quality=quality,
        n=1,
        response_format="url",
    )
    url = resp.data[0].url
    urllib.request.urlretrieve(url, dest)
    print(f"  ✓ saved {dest.relative_to(Path(__file__).parent)}")


def main() -> None:
    print(f"Generating {len(PRODUCTS)} product images into {OUT}/\n")
    for i, (slug, prompt) in enumerate(PRODUCTS.items(), 1):
        try:
            generate(slug, prompt)
        except Exception as e:
            print(f"  ✗ {slug} failed: {e}")
        # Light rate-limit buffer; OpenAI allows 5/min at tier 1
        if i < len(PRODUCTS):
            time.sleep(1)
    print("\nDone. To publish:")
    print("  git add static/products")
    print("  git commit -m 'Add AI-generated product photos'")
    print("  git push")
    print("\nThen ping me and I'll flip product_images.py to /static/products/<slug>.png.")


if __name__ == "__main__":
    main()
