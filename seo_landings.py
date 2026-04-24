"""crittr.ai — SEO landing pages (Phase 7.1).

Renders `/c/<slug>` themed landing pages for common triage queries.
Each page:
  * Human-readable headline + 2-paragraph intro
  * Hero chat widget pre-filled with the topic
  * A 3-bullet "what to watch for" block
  * Internal links to 3 related topics
  * Shared footer (imported from the main template where possible)

Slug format
-----------
Kebab-case: "dog-ate-grapes", "cat-throwing-up-foam".
The slug maps to a `Topic` record with species + headline + triage
hints. Topics live in SEO_TOPICS below — seed with ~50 high-volume
long-tail queries; extend over time.

Public API
----------
    register_seo_landings(app) -> None
    TOPICS -> dict[slug, Topic]   (also exported for sitemap generation)
"""
import logging
from dataclasses import dataclass, field
from typing import List
from flask import render_template_string, abort, Response

log = logging.getLogger("crittr.seo")


# ---------------------------------------------------------------
# Topic catalog
# ---------------------------------------------------------------
@dataclass
class Topic:
    slug: str
    species: str         # "dog" | "cat"
    title: str           # "Dog ate grapes"
    question: str        # prefilled into the hero chat
    meta_description: str
    watch_for: List[str]
    lean: str            # "ER NOW" | "VET TOMORROW" | "SAFE AT HOME"
    related: List[str] = field(default_factory=list)
    faqs: List[tuple] = field(default_factory=list)  # [(question, answer), ...] — empty = auto-generate


# Seed catalog. Copy is deliberately specific — thin pages don't rank.
_TOPICS = [
    Topic(
        slug="dog-ate-grapes",
        species="dog",
        title="My dog ate grapes — what now?",
        question="My dog ate grapes. What should I do?",
        meta_description=(
            "Your dog just ate grapes. Grapes are toxic to dogs at any dose. "
            "Here's exactly what to do in the next 30 minutes."
        ),
        watch_for=[
            "Vomiting or repeated retching within 2–4 hours",
            "Lethargy or refusal to drink in the next 12 hours",
            "Decreased urination over the next 24–48 hours (kidney sign)",
        ],
        lean="ER NOW",
        related=["dog-ate-chocolate", "dog-ate-xylitol", "puppy-not-eating"],
    ),
    Topic(
        slug="dog-ate-chocolate",
        species="dog",
        title="My dog ate chocolate — is it an emergency?",
        question="My dog ate chocolate. How much is too much?",
        meta_description=(
            "Chocolate toxicity in dogs is dose- and type-dependent. "
            "Dark chocolate is the worst. Here's how to decide when to rush in."
        ),
        watch_for=[
            "Restlessness, hyperactivity, or shaking",
            "Vomiting or diarrhea in the next 6 hours",
            "Rapid heart rate or unsteady walking",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-grapes", "dog-ate-xylitol", "dog-ate-onion"],
    ),
    Topic(
        slug="dog-ate-xylitol",
        species="dog",
        title="My dog ate xylitol — what to do",
        question="My dog ate something with xylitol in it. What now?",
        meta_description=(
            "Xylitol (found in sugar-free gum and peanut butter) causes "
            "rapid, life-threatening blood-sugar drops in dogs. Don't wait."
        ),
        watch_for=[
            "Weakness or collapse within 30–60 minutes",
            "Vomiting, tremors, or a seizure",
            "Disorientation or stumbling",
        ],
        lean="ER NOW",
        related=["dog-ate-grapes", "dog-ate-chocolate", "puppy-not-eating"],
    ),
    Topic(
        slug="cat-throwing-up-foam",
        species="cat",
        title="My cat is throwing up foam — should I worry?",
        question="My cat is throwing up foam. What does that mean?",
        meta_description=(
            "Foamy vomit in cats can be routine (hairball, empty stomach) "
            "or a red flag. Here's how to tell the difference."
        ),
        watch_for=[
            "More than 3 episodes in 24 hours",
            "Lethargy or refusal to eat past the next meal",
            "Any blood streaks in the foam",
        ],
        lean="SAFE AT HOME",
        related=["cat-not-eating", "cat-sneezing",
                 "cat-throwing-up-hairball"],
    ),
    Topic(
        slug="dog-limping-after-walk",
        species="dog",
        title="My dog is limping after a walk — when is it serious?",
        question="My dog is limping after a walk. When should I go to the vet?",
        meta_description=(
            "Most limps that show up after a walk resolve with rest. Here's "
            "when it's time to stop monitoring and book a visit."
        ),
        watch_for=[
            "Swelling, heat, or a visible wound",
            "Limping that's worse the next morning",
            "Refusal to bear any weight at all",
        ],
        lean="VET TOMORROW",
        related=["tick-removal", "puppy-not-eating", "dog-ear-infection"],
    ),
    Topic(
        slug="puppy-not-eating",
        species="dog",
        title="My puppy isn't eating — what to check",
        question="My puppy hasn't eaten today. Should I be worried?",
        meta_description=(
            "Puppies skip meals for all kinds of reasons. Some are fine; some "
            "are dangerous. Here's what to check before deciding to wait."
        ),
        watch_for=[
            "No water intake either, especially under 3 months old",
            "Lethargy, cold gums, or sunken eyes",
            "Vomiting or diarrhea alongside",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-grapes", "dog-limping-after-walk",
                 "puppy-diarrhea"],
    ),
    Topic(
        slug="cat-not-eating",
        species="cat",
        title="My cat stopped eating — how long is too long?",
        question="My cat hasn't eaten in a day. When should I worry?",
        meta_description=(
            "Cats can develop fatty-liver disease from just a few days of "
            "not eating. The 24–48 hour window matters more than people think."
        ),
        watch_for=[
            "Yellowing of the gums or eyes",
            "Vomiting, hiding, or not drinking either",
            "Already underweight or a senior cat",
        ],
        lean="VET TOMORROW",
        related=["cat-throwing-up-foam", "cat-sneezing",
                 "cat-throwing-up-hairball"],
    ),
    Topic(
        slug="dog-ate-onion",
        species="dog",
        title="My dog ate onion — how serious is it?",
        question="My dog ate onion. What do I need to watch for?",
        meta_description=(
            "Onion contains thiosulfate, which damages a dog's red blood cells. "
            "Even small amounts can cause hemolytic anemia over a few days."
        ),
        watch_for=[
            "Pale gums or weakness 1–5 days after ingestion",
            "Dark brown or bloody urine",
            "Rapid breathing, reluctance to move",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-garlic", "dog-ate-grapes", "dog-ate-chocolate"],
    ),
    Topic(
        slug="dog-ate-garlic",
        species="dog",
        title="My dog ate garlic — is that toxic?",
        question="My dog ate garlic. Is that dangerous?",
        meta_description=(
            "Garlic is roughly 5x more toxic to dogs than onion by weight. "
            "Small single exposures often pass; repeated doses build up."
        ),
        watch_for=[
            "Vomiting or diarrhea in the first 24 hours",
            "Pale gums, lethargy, or collapse 1–5 days later",
            "Any breed with known sensitivity (Akita, Shiba Inu, Japanese breeds)",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-onion", "dog-ate-grapes", "dog-ate-chocolate"],
    ),
    Topic(
        slug="dog-ate-raisins",
        species="dog",
        title="My dog ate raisins — emergency?",
        question="My dog ate raisins. How much is dangerous?",
        meta_description=(
            "Raisins are concentrated grapes — dose for dose, more toxic. "
            "There's no established safe amount. Treat every exposure seriously."
        ),
        watch_for=[
            "Vomiting or retching within 2–4 hours",
            "Reduced urination in the next 24–48 hours",
            "Lethargy, wobbly walk, or bad breath (uremia)",
        ],
        lean="ER NOW",
        related=["dog-ate-grapes", "dog-ate-chocolate", "dog-ate-xylitol"],
    ),
    Topic(
        slug="dog-ate-avocado",
        species="dog",
        title="My dog ate avocado — should I worry?",
        question="My dog ate avocado. Is that toxic?",
        meta_description=(
            "The flesh of avocado is mostly fine for dogs in small amounts. "
            "The pit is the real risk — choking and intestinal obstruction."
        ),
        watch_for=[
            "Gagging or repeated retching (possible pit)",
            "Vomiting more than twice, or dry heaves",
            "Belly that feels tight or painful to touch",
        ],
        lean="SAFE AT HOME",
        related=["dog-ate-bone", "dog-ate-chocolate", "puppy-not-eating"],
    ),
    Topic(
        slug="dog-ate-ibuprofen",
        species="dog",
        title="My dog ate ibuprofen (Advil) — what now?",
        question="My dog ate an ibuprofen pill. What should I do?",
        meta_description=(
            "Ibuprofen is toxic to dogs at doses people consider normal. "
            "Even one 200mg tablet can injure the stomach of a small dog."
        ),
        watch_for=[
            "Vomiting (sometimes with blood) within a few hours",
            "Black, tarry stool over the next 1–2 days",
            "Decreased urination, lethargy (kidney sign)",
        ],
        lean="ER NOW",
        related=["dog-ate-acetaminophen", "dog-ate-xylitol", "dog-ate-grapes"],
    ),
    Topic(
        slug="dog-ate-acetaminophen",
        species="dog",
        title="My dog ate Tylenol (acetaminophen) — emergency?",
        question="My dog ate acetaminophen. Is that bad?",
        meta_description=(
            "Acetaminophen damages a dog's liver and red blood cells. "
            "Cats are even more sensitive — a single regular-strength tablet can kill."
        ),
        watch_for=[
            "Brown/blue gums or dark urine (methemoglobinemia)",
            "Vomiting, drooling, or loss of appetite",
            "Facial or paw swelling",
        ],
        lean="ER NOW",
        related=["dog-ate-ibuprofen", "cat-ate-lily", "dog-ate-xylitol"],
    ),
    Topic(
        slug="cat-ate-lily",
        species="cat",
        title="My cat chewed on a lily — what now?",
        question="My cat chewed on a lily plant. Is that dangerous?",
        meta_description=(
            "True lilies (Lilium and Hemerocallis) cause acute kidney failure in "
            "cats within 24–72 hours. Even pollen or vase water can do it."
        ),
        watch_for=[
            "Vomiting, drooling, or hiding within a few hours",
            "Reduced or no urination over 1–2 days",
            "Any plant material visible in the mouth or fur",
        ],
        lean="ER NOW",
        related=["cat-not-eating", "cat-throwing-up-foam", "dog-ate-grapes"],
    ),
    Topic(
        slug="dog-vomiting-yellow",
        species="dog",
        title="My dog is vomiting yellow foam — what does it mean?",
        question="My dog keeps throwing up yellow foam. Should I worry?",
        meta_description=(
            "Yellow foam is stomach bile. In dogs, an occasional morning "
            "episode is usually bilious vomiting syndrome and benign."
        ),
        watch_for=[
            "More than 2 episodes in 24 hours",
            "Any blood or coffee-ground material in the vomit",
            "Belly looks bloated or painful",
        ],
        lean="SAFE AT HOME",
        related=["dog-diarrhea", "dog-ate-bone", "puppy-not-eating"],
    ),
    Topic(
        slug="dog-diarrhea",
        species="dog",
        title="My dog has diarrhea — when do I need the vet?",
        question="My dog has diarrhea. How long should I wait?",
        meta_description=(
            "Most dog diarrhea resolves in 24–48 hours with a bland diet. "
            "A short list of red flags tells you when to stop waiting."
        ),
        watch_for=[
            "Frank red blood or black, tarry stool",
            "Lethargy, refusal to drink, or sunken eyes",
            "Puppy under 6 months, or any lasting past 48 hours",
        ],
        lean="SAFE AT HOME",
        related=["dog-vomiting-yellow", "puppy-not-eating", "dog-ate-bone"],
    ),
    Topic(
        slug="dog-ate-bone",
        species="dog",
        title="My dog swallowed a bone — is it dangerous?",
        question="My dog just swallowed a cooked bone. What should I do?",
        meta_description=(
            "Cooked bones splinter and can perforate the GI tract; raw bones "
            "tend to pass. Size of the bone vs size of the dog matters most."
        ),
        watch_for=[
            "Retching, drooling, or pawing at the mouth",
            "Refusal to eat or drink past the next meal",
            "Belly tense or painful; dark/bloody stool",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-avocado", "dog-vomiting-yellow", "dog-diarrhea"],
    ),
    Topic(
        slug="cat-constipation",
        species="cat",
        title="My cat is constipated — how long is too long?",
        question="My cat hasn't pooped in a couple days. What should I do?",
        meta_description=(
            "Occasional constipation is common in cats — chronic cases can "
            "progress to megacolon. The 72-hour mark is a useful threshold."
        ),
        watch_for=[
            "More than 72 hours without a bowel movement",
            "Straining in the litter box with little or no output",
            "Vomiting, lethargy, or bloated belly",
        ],
        lean="VET TOMORROW",
        related=["cat-not-eating", "cat-uti", "cat-throwing-up-foam"],
    ),
    Topic(
        slug="dog-shaking",
        species="dog",
        title="My dog is shaking — what could it be?",
        question="My dog is shaking and I don't know why. What should I check?",
        meta_description=(
            "Shaking in dogs can mean cold, fear, pain, nausea, or toxin "
            "exposure. Context — and what changed in the last 24 hours — matters."
        ),
        watch_for=[
            "Recent access to chocolate, xylitol, marijuana, or human meds",
            "Shaking plus vomiting, stumbling, or glazed eyes",
            "Shaking that doesn't stop with warmth and calm",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-chocolate", "dog-ate-xylitol", "dog-limping-after-walk"],
    ),
    Topic(
        slug="dog-ear-infection",
        species="dog",
        title="My dog has a smelly ear — is it an ear infection?",
        question="My dog's ear is smelly and he keeps scratching. What is it?",
        meta_description=(
            "Smelly discharge plus head shaking is almost always an ear "
            "infection. Waiting can push it from outer ear to middle ear."
        ),
        watch_for=[
            "Head tilt, balance trouble, or eye flicking (middle-ear spread)",
            "Dark debris that looks like coffee grounds (possible mites)",
            "Swollen, hot flap — can be an aural hematoma",
        ],
        lean="VET TOMORROW",
        related=["dog-hot-spot", "dog-limping-after-walk", "dog-diarrhea"],
    ),
    Topic(
        slug="dog-hot-spot",
        species="dog",
        title="My dog has a hot spot — what should I do tonight?",
        question="My dog has a raw, wet spot on his skin. How do I treat it?",
        meta_description=(
            "Hot spots (acute moist dermatitis) spread fast if left wet and "
            "licked. Keep it dry, keep it covered, and block the tongue."
        ),
        watch_for=[
            "Spreading redness past the original patch in 24 hours",
            "Yellow pus, thick crust, or fever (systemic infection)",
            "Hot spot on the ear flap — usually needs an oral antibiotic",
        ],
        lean="SAFE AT HOME",
        related=["dog-ear-infection", "dog-limping-after-walk", "dog-diarrhea"],
    ),
    Topic(
        slug="cat-uti",
        species="cat",
        title="My cat is straining in the litter box — UTI or blockage?",
        question="My cat is straining to pee. Is that a urinary blockage?",
        meta_description=(
            "A male cat straining with no urine output is an emergency — a "
            "full urethral blockage can be fatal within 24–48 hours."
        ),
        watch_for=[
            "No urine produced despite repeated straining (male cats)",
            "Crying in the box, licking genitals, vomiting",
            "Blood in urine or urinating outside the box",
        ],
        lean="ER NOW",
        related=["cat-not-eating", "cat-constipation", "cat-throwing-up-foam"],
    ),
    Topic(
        slug="dog-panting-heavily",
        species="dog",
        title="My dog is panting heavily — should I be worried?",
        question="My dog is panting heavily and it's not hot out. What's going on?",
        meta_description=(
            "Heavy panting with no heat or exercise can mean pain, anxiety, heatstroke, "
            "or heart trouble. Here's how to tell the difference in the next 20 minutes."
        ),
        watch_for=[
            "Gums that are brick-red, pale, blue, or tacky",
            "Refusing to lie down, or restless pacing with the panting",
            "Body temperature above 103.5°F or collapse",
        ],
        lean="VET TOMORROW",
        related=["dog-shaking", "dog-bloated-stomach", "dog-excessive-thirst"],
    ),
    Topic(
        slug="dog-ate-gum",
        species="dog",
        title="My dog ate gum — is it sugar-free (xylitol)?",
        question="My dog just ate a piece of gum. What do I do?",
        meta_description=(
            "If the gum contains xylitol, this is a true emergency even at one or two pieces. "
            "Check the ingredient list now — then act within minutes."
        ),
        watch_for=[
            "Vomiting, weakness, or wobbliness within 30–60 minutes",
            "Seizures or collapse (xylitol hypoglycemia)",
            "Any sugar-free / 'diet' / 'sugarless' label on the wrapper",
        ],
        lean="ER NOW",
        related=["dog-ate-xylitol", "dog-ate-chocolate", "dog-ate-grapes"],
    ),
    Topic(
        slug="dog-ate-mushroom",
        species="dog",
        title="My dog ate a mushroom in the yard — is it toxic?",
        question="My dog ate a wild mushroom from the yard. What should I do?",
        meta_description=(
            "Assume any wild mushroom is toxic until proven otherwise. Some species cause liver failure "
            "within 6–24 hours with no early warning. Here's the playbook."
        ),
        watch_for=[
            "Vomiting, drooling, or diarrhea in the first 6 hours",
            "Yellowing of gums or eyes (liver sign) in 12–48 hours",
            "Stumbling, tremors, or hallucinations (neurotoxic species)",
        ],
        lean="ER NOW",
        related=["dog-ate-grapes", "dog-ate-onion", "dog-vomiting-yellow"],
    ),
    Topic(
        slug="dog-ate-rat-poison",
        species="dog",
        title="My dog ate rat poison — how urgent is this?",
        question="My dog ate rat poison. What do I do right now?",
        meta_description=(
            "Rodenticide is a true emergency — don't wait for symptoms. Bring the packaging with you. "
            "The active ingredient determines the antidote and the window."
        ),
        watch_for=[
            "Bruising, bleeding gums, or bloody urine (anticoagulants, 3–7 days out)",
            "Tremors, seizures, or weakness (bromethalin, within 24 hours)",
            "Excessive thirst and urination (cholecalciferol, 24–72 hours)",
        ],
        lean="ER NOW",
        related=["dog-ate-ibuprofen", "dog-ate-acetaminophen", "dog-shaking"],
    ),
    Topic(
        slug="dog-ate-battery",
        species="dog",
        title="My dog chewed a battery — what are the risks?",
        question="My dog chewed a battery. Is it an emergency?",
        meta_description=(
            "Chewed alkaline or lithium batteries can cause severe chemical burns in the mouth and throat. "
            "Swallowed button batteries can erode the esophagus within hours."
        ),
        watch_for=[
            "Drooling, pawing at the mouth, or refusing food",
            "Vomiting (don't induce — caustic reflux makes it worse)",
            "Coughing, gagging, or black/bloody stool",
        ],
        lean="ER NOW",
        related=["dog-ate-sock", "dog-ate-bone", "dog-vomiting-yellow"],
    ),
    Topic(
        slug="dog-ate-sock",
        species="dog",
        title="My dog swallowed a sock — will it pass?",
        question="My dog swallowed a sock. Will it come out on its own?",
        meta_description=(
            "Socks often pass in small dogs or get stuck and become a surgical emergency. "
            "Size of dog vs. size of sock is the key variable. Here's how to read it."
        ),
        watch_for=[
            "Repeated vomiting, especially after eating",
            "Refusal to eat, bloated or painful belly",
            "No bowel movement for 24+ hours",
        ],
        lean="VET TOMORROW",
        related=["dog-ate-bone", "dog-ate-battery", "dog-vomiting-yellow"],
    ),
    Topic(
        slug="cat-ate-chocolate",
        species="cat",
        title="My cat ate chocolate — is it dangerous for cats too?",
        question="My cat licked chocolate off a plate. Is that bad?",
        meta_description=(
            "Cats are less drawn to chocolate than dogs but just as vulnerable to theobromine toxicity. "
            "Dark and baking chocolate are the real problems."
        ),
        watch_for=[
            "Vomiting, diarrhea, or unusual thirst in the first 6 hours",
            "Rapid heartbeat or restlessness",
            "Muscle tremors or seizures (high dose)",
        ],
        lean="VET TOMORROW",
        related=["cat-ate-string", "cat-not-eating", "cat-ate-lily"],
    ),
    Topic(
        slug="cat-ate-string",
        species="cat",
        title="My cat swallowed string — should I pull it out?",
        question="My cat swallowed a piece of string. Should I pull it out?",
        meta_description=(
            "Never pull visible string from a cat's mouth or bottom — linear foreign bodies can slice "
            "through the intestines as they tighten. This needs imaging, not force."
        ),
        watch_for=[
            "String still visible at the mouth or anus (don't pull)",
            "Vomiting, refusing food, or hiding",
            "Painful, bloated belly or straining with no stool",
        ],
        lean="ER NOW",
        related=["cat-not-eating", "cat-throwing-up-foam", "cat-constipation"],
    ),
    Topic(
        slug="cat-throwing-up-hairball",
        species="cat",
        title="My cat is throwing up hairballs — how often is too often?",
        question="My cat keeps throwing up hairballs. Is that normal?",
        meta_description=(
            "An occasional hairball is normal — more than one a week, or retching with nothing coming up, "
            "usually isn't. It's often a sign of GI motility or inflammation, not just grooming."
        ),
        watch_for=[
            "More than one hairball per week or daily retching",
            "Weight loss, decreased appetite, or diarrhea alongside",
            "Retching with nothing produced (could be partial blockage)",
        ],
        lean="VET TOMORROW",
        related=["cat-not-eating", "cat-sneezing", "cat-constipation"],
    ),
    Topic(
        slug="cat-sneezing",
        species="cat",
        title="My cat won't stop sneezing — is it a cold?",
        question="My cat has been sneezing all day. What's going on?",
        meta_description=(
            "Most cat sneezing is feline upper respiratory infection (herpesvirus or calicivirus). "
            "Some cases are dental, fungal, or a stuck blade of grass. Here's how to sort them out."
        ),
        watch_for=[
            "Yellow or green nasal discharge (not clear)",
            "Eye discharge, squinting, or ulcers",
            "Refusing to eat for more than 24 hours (a cat needs its nose to eat)",
        ],
        lean="VET TOMORROW",
        related=["cat-not-eating", "cat-ate-string", "kitten-not-eating"],
    ),
    Topic(
        slug="dog-bloated-stomach",
        species="dog",
        title="My dog's stomach looks bloated — is it GDV?",
        question="My dog's belly looks huge and tight. Is this bloat?",
        meta_description=(
            "Gastric dilatation-volvulus (GDV / 'bloat') is a deep-chested-dog emergency. "
            "Every hour without surgery increases mortality sharply."
        ),
        watch_for=[
            "Visibly distended, drum-tight belly",
            "Unproductive retching — trying to vomit with nothing coming up",
            "Restlessness, pacing, drooling, and rapid breathing",
        ],
        lean="ER NOW",
        related=["dog-vomiting-yellow", "dog-shaking", "dog-panting-heavily"],
    ),
    Topic(
        slug="dog-scooting",
        species="dog",
        title="My dog is scooting its bottom — anal glands?",
        question="My dog keeps scooting its butt on the floor. Why?",
        meta_description=(
            "Scooting is usually anal gland irritation, but it can also be worms, allergies, or an infected "
            "anal sac. Most cases are not emergencies — a few are."
        ),
        watch_for=[
            "Visible swelling or red/dark spot near the anus (possible abscess)",
            "Strong fishy smell or licking constantly",
            "Rice-like segments in stool or around the bottom (tapeworms)",
        ],
        lean="SAFE AT HOME",
        related=["dog-diarrhea", "dog-ear-infection", "dog-hot-spot"],
    ),
    Topic(
        slug="dog-excessive-thirst",
        species="dog",
        title="My dog is drinking way more water than usual — why?",
        question="My dog is suddenly drinking a ton of water. What could that mean?",
        meta_description=(
            "A sudden, sustained jump in water intake (polydipsia) is a classic early sign of diabetes, "
            "Cushing's, kidney disease, or uterine infection. Worth testing, not ignoring."
        ),
        watch_for=[
            "Urinating far more often or inside the house after being trained",
            "Weight loss with a big appetite (diabetes) or pot-bellied look (Cushing's)",
            "Lethargy, vomiting, or a known unspayed female in heat recently (pyometra)",
        ],
        lean="VET TOMORROW",
        related=["dog-panting-heavily", "dog-bad-breath", "dog-vomiting-yellow"],
    ),
    Topic(
        slug="dog-bad-breath",
        species="dog",
        title="My dog's breath is awful — is it just teeth?",
        question="My dog's breath smells terrible. Is that normal?",
        meta_description=(
            "'Dog breath' shouldn't knock you over. Persistent foul breath usually means periodontal disease; "
            "fruity or chemical breath can signal diabetes or kidney issues."
        ),
        watch_for=[
            "Brown tartar, red gums, or loose teeth",
            "Sweet/fruity breath (possible diabetes / ketoacidosis)",
            "Ammonia-like breath plus vomiting (possible kidney disease)",
        ],
        lean="VET TOMORROW",
        related=["dog-excessive-thirst", "dog-ear-infection", "dog-vomiting-yellow"],
    ),
    Topic(
        slug="puppy-diarrhea",
        species="dog",
        title="My puppy has diarrhea — how worried should I be?",
        question="My puppy has diarrhea. Is this an emergency?",
        meta_description=(
            "Puppies dehydrate and crash fast. Bloody diarrhea, lethargy, or a puppy under 16 weeks "
            "that hasn't finished vaccinations is a parvovirus red flag."
        ),
        watch_for=[
            "Bloody or raspberry-jam-colored stool",
            "Vomiting along with the diarrhea, or refusing water",
            "Lethargy, sunken eyes, or gums that feel tacky",
        ],
        lean="ER NOW",
        related=["puppy-not-eating", "dog-diarrhea", "dog-vomiting-yellow"],
    ),
    Topic(
        slug="kitten-not-eating",
        species="cat",
        title="My kitten won't eat — how long is too long?",
        question="My kitten hasn't eaten in a day. Should I worry?",
        meta_description=(
            "A kitten that skips more than 12 hours of food is at real risk of hypoglycemia and "
            "hepatic lipidosis. Much tighter window than an adult cat."
        ),
        watch_for=[
            "Wobbliness, cold ears/paws, or collapse (low blood sugar)",
            "Diarrhea or vomiting alongside the not-eating",
            "Dehydration — tacky gums, skin tent, sunken eyes",
        ],
        lean="ER NOW",
        related=["cat-not-eating", "cat-sneezing", "cat-throwing-up-hairball"],
    ),
    Topic(
        slug="dog-red-eyes",
        species="dog",
        title="My dog's eyes are red — allergies or emergency?",
        question="My dog has red, irritated eyes. What should I do?",
        meta_description=(
            "Red eyes can be allergies, dry eye, a scratched cornea, or glaucoma. "
            "Squinting plus a cloudy eye is an emergency — pressure can damage vision within hours."
        ),
        watch_for=[
            "Squinting, pawing at the eye, or holding it shut",
            "Cloudy or bluish cornea (possible glaucoma)",
            "Yellow or green discharge, or an obvious injury",
        ],
        lean="VET TOMORROW",
        related=["dog-ear-infection", "dog-hot-spot", "dog-shaking"],
    ),
    Topic(
        slug="tick-removal",
        species="dog",
        title="I found a tick on my dog — how do I remove it safely?",
        question="I found a tick on my dog. How should I remove it?",
        meta_description=(
            "Use fine-tipped tweezers, grip where the tick meets the skin, and pull straight out with "
            "steady pressure. Skip the matches, alcohol, and petroleum jelly — those make it worse."
        ),
        watch_for=[
            "Lameness, fever, or lethargy 1–3 weeks later (possible tick-borne disease)",
            "A target-shaped rash or swollen joints",
            "Head of the tick left behind — the skin usually heals without intervention",
        ],
        lean="SAFE AT HOME",
        related=["dog-limping-after-walk", "dog-hot-spot", "dog-red-eyes"],
    ),
]

TOPICS = {t.slug: t for t in _TOPICS}


# ---------------------------------------------------------------
# Template
# ---------------------------------------------------------------
_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ topic.title }} — crittr.ai</title>
  <meta name="description" content="{{ topic.meta_description }}">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <!-- Open Graph -->
  <meta property="og:type" content="article">
  <meta property="og:title" content="{{ topic.title }} — crittr.ai">
  <meta property="og:description" content="{{ topic.meta_description }}">
  <meta property="og:url" content="https://crittr.ai/c/{{ topic.slug }}">
  <meta property="og:image" content="https://crittr.ai/og/c-{{ topic.slug }}.png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:image" content="https://crittr.ai/og/c-{{ topic.slug }}.png">
  <meta name="twitter:card" content="summary_large_image">
  <!-- JSON-LD -->
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "MedicalWebPage",
    "name": {{ topic.title | tojson }},
    "description": {{ topic.meta_description | tojson }},
    "about": {
      "@type": "MedicalCondition",
      "name": {{ topic.title | tojson }}
    },
    "audience": {
      "@type": "Audience",
      "audienceType": "Pet owners"
    }
  }
  </script>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
      {% for q, a in faqs %}
      {
        "@type": "Question",
        "name": {{ q | tojson }},
        "acceptedAnswer": {
          "@type": "Answer",
          "text": {{ a | tojson }}
        }
      }{% if not loop.last %},{% endif %}
      {% endfor %}
    ]
  }
  </script>
  <style>
    :root {
      --bg:#FBF7EE; --ink:#2A2A2A; --muted:#6B6B6B; --accent:#6FA26F;
      --card:#FFFFFF; --line:#E7E1D2; --er:#C84A3A; --vet:#D9A23A;
    }
    body { margin:0; font-family:Inter,system-ui,sans-serif;
           background:var(--bg); color:var(--ink); line-height:1.55; }
    header { padding:20px 32px; border-bottom:1px solid var(--line); display:flex; align-items:center; justify-content:space-between; background:#FDFBF5; }
    header a.logo { color:#2D4A30; text-decoration:none; font-weight:700; font-family:'Fraunces',serif; font-size:1.88rem; letter-spacing:-.028em; display:flex; align-items:center; gap:.55rem; }
    header a.logo .logo-dot { width:16px; height:16px; border-radius:50%; background:#6B9E6B; box-shadow:0 0 0 4px #E4EFE2; display:inline-block; }
    header .back { color:#6E7D70; font-size:.9rem; text-decoration:none; font-weight:500; }
    header .back:hover { color:#3E6340; }
    main { max-width:780px; margin:0 auto; padding:40px 24px; }
    h1 { font-family:'Fraunces',serif; font-weight:500; font-size:40px;
         line-height:1.15; margin:0 0 16px 0; }
    .lean { display:inline-block; padding:3px 10px; border-radius:999px;
            font-size:12px; letter-spacing:0.04em; text-transform:uppercase;
            font-weight:500; background:var(--line); color:var(--muted);
            margin-bottom:12px; }
    .lean.er { background:#FDF1EF; color:var(--er); }
    .lean.vet { background:#FCF6E8; color:var(--vet); }
    .lean.safe { background:#EEF5EA; color:var(--accent); }
    .card { background:var(--card); border:1px solid var(--line);
            border-radius:12px; padding:24px; margin:24px 0; }
    .hero-chat textarea {
      width:100%; box-sizing:border-box; padding:12px; font-size:16px;
      font-family:inherit; border:1px solid var(--line); border-radius:8px;
      resize:vertical; min-height:80px;
    }
    .hero-chat .send {
      background:var(--ink); color:white; border:none; padding:10px 20px;
      border-radius:8px; margin-top:12px; font-family:inherit; font-size:15px;
      cursor:pointer;
    }
    .watch-for h3 { margin:0 0 8px 0; font-size:15px;
                    letter-spacing:0.04em; text-transform:uppercase;
                    color:var(--muted); }
    .watch-for ul { margin:0; padding:0; list-style:none; }
    .watch-for li { padding:8px 0; border-bottom:1px dashed var(--line);
                    font-size:15px; }
    .watch-for li:last-child { border-bottom:none; }
    .related { display:grid; grid-template-columns:repeat(3, 1fr); gap:12px;
               margin-top:32px; }
    .related a { display:block; padding:12px; background:var(--card);
                 border:1px solid var(--line); border-radius:10px;
                 text-decoration:none; color:var(--ink); font-size:14px; }
    .related a:hover { border-color:var(--accent); }
    footer { text-align:center; padding:32px 24px; color:var(--muted);
             font-size:13px; border-top:1px solid var(--line); margin-top:40px; }
    @media(max-width:640px) {
      h1 { font-size:30px; }
      .related { grid-template-columns:1fr; }
    }
  
    .card.picks { background: #FDFBF5; }
    .picks-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-top: 6px; }
    @media (max-width: 640px) { .picks-grid { grid-template-columns: 1fr; } }
    .pick { display: flex; flex-direction: column; background: #fff; border: 1px solid var(--line);
             border-radius: 12px; padding: 14px; text-decoration: none; color: var(--ink);
             transition: border-color .2s ease, transform .15s ease; }
    .pick:hover { border-color: #A6C9A2; transform: translateY(-1px); }
    .pick-name { font-weight: 600; font-size: 14.5px; line-height: 1.3; color: #1F3221;
                 font-family: 'Fraunces', serif; }
    .pick-blurb { font-size: 12.5px; color: var(--muted); line-height: 1.45; margin-top: 6px; flex: 1; }
    .pick-cta { font-size: 13px; font-weight: 600; color: #3E6340; margin-top: 6px; }
    .pick-price { font-size: 11.5px; color: #3E6340; font-weight: 600; margin-top: 6px; letter-spacing: .01em; }
</style>
</head>
<body>
  <header>
    <a href="/" class="logo"><span class="logo-dot"></span>crittr</a>
    <a href="/#hero-chat" class="back">← Back to triage</a>
  </header>
  <main>
    <span class="lean {{ lean_class }}">Tends to be: {{ topic.lean }}</span>
    <h1>{{ topic.title }}</h1>
    <p>{{ topic.meta_description }}</p>

    <div class="card hero-chat">
      <h3 style="margin:0 0 12px 0; font-family:'Fraunces',serif; font-weight:500;">
        Tell crittr what's happening
      </h3>
      <textarea id="q" placeholder="Describe the situation…">{{ topic.question }}</textarea>
      <button class="send" onclick="go()">Get a triage read</button>
    </div>

    <div class="card watch-for">
      <h3>What to watch for</h3>
      <ul>
        {% for w in topic.watch_for %}<li>{{ w }}</li>{% endfor %}
      </ul>
    </div>

    {% if picks %}
    <div class="card picks">
      <h3 style="margin:0 0 4px 0; font-family:'Fraunces',serif; font-weight:500;">
        What our vet advisors recommend
      </h3>
      <p style="margin:0 0 14px 0; font-size:14px; color:var(--muted); line-height:1.5;">
        Over-the-counter picks that commonly help with this. Not a substitute for a vet visit — if symptoms escalate, book one.
      </p>
      <div class="picks-grid">
        {% for p in picks %}
          <a class="pick" href="{{ p.amazon_url }}" target="_blank" rel="nofollow noopener sponsored">
            <div class="pick-name">{{ p.public_name or p.slug }}</div>
            {% if p.public_blurb %}<div class="pick-blurb">{{ p.public_blurb }}</div>{% endif %}
            <div class="pick-price">Best price found at Amazon</div>
            <div class="pick-cta">Buy now →</div>
          </a>
        {% endfor %}
      </div>
      <div style="font-size:11.5px; color:var(--muted); margin-top:12px; line-height:1.5;">
        As an Amazon Associate, crittr.ai earns from qualifying purchases at no extra cost to you.
      </div>
    </div>
    {% endif %}

    {% if faqs %}
    <div class="card faq">
      <h3 style="margin:0 0 12px 0; font-family:'Fraunces',serif; font-weight:500;">
        Common questions
      </h3>
      <div>
        {% for q, a in faqs %}
        <details style="border-bottom:1px dashed var(--line); padding:10px 0;">
          <summary style="cursor:pointer; font-weight:500; font-size:15px;">{{ q }}</summary>
          <p style="margin:8px 0 4px 0; font-size:14.5px; color:var(--muted); line-height:1.55;">{{ a }}</p>
        </details>
        {% endfor %}
      </div>
    </div>
    {% endif %}

    <div class="related">
      {% for slug in topic.related %}
        {% if slug in all_topics %}
          <a href="/c/{{ slug }}">{{ all_topics[slug].title }}</a>
        {% endif %}
      {% endfor %}
    </div>
  </main>
  <footer>
    crittr.ai is not a substitute for a veterinary exam.<br>
    In a true emergency, go to the nearest animal hospital.
  </footer>
  <script>
    // Posts to the anon hero-chat endpoint and replaces the card with the reply.
    async function go() {
      const q = document.getElementById('q').value.trim();
      if (!q) return;
      const wrap = document.querySelector('.hero-chat');
      wrap.innerHTML = '<p>Thinking…</p>';
      try {
        const r = await fetch('/api/chat/anon', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message: q, hint: {{ topic.species | tojson }}}),
        });
        const data = await r.json();
        wrap.innerHTML =
          '<div style="white-space:pre-wrap">' +
          (data.reply || '').replace(/</g, '&lt;') +
          '</div>';
      } catch (e) {
        wrap.innerHTML = '<p>Sorry — try again.</p>';
      }
    }
  </script>
</body>
</html>"""


_LEAN_CLASS = {"ER NOW": "er", "VET TOMORROW": "vet", "SAFE AT HOME": "safe"}


# ---------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------
def _sitemap_xml(base_url="https://crittr.ai"):
    urls = [f"{base_url}/"]
    # Phase A — MEDVi-style category shop pages
    for slug in ("dogs", "cats", "supplements", "rx"):
        urls.append(f"{base_url}/shop/{slug}")
    urls.extend(f"{base_url}/c/{slug}" for slug in TOPICS)
    items = "".join(
        f"<url><loc>{u}</loc><changefreq>weekly</changefreq></url>"
        for u in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{items}</urlset>"
    )



# ---------------------------------------------------------------
# FAQ generation (FAQPage JSON-LD)
# ---------------------------------------------------------------
_LEAN_FAQ_BASE = {
    "ER NOW": (
        "Should I go to the emergency vet right now?",
        "Yes. Based on what you\'ve described, this is the kind of situation where minutes can matter. "
        "Call your nearest 24/7 animal ER on the way. If you\'re not sure where that is, use our free triage chat above — "
        "it\'ll confirm the urgency and surface a local ER in seconds.",
    ),
    "VET TOMORROW": (
        "Can this wait until the morning?",
        "In most cases, yes — a same-day or next-morning vet visit is the right call, not an ER run. "
        "Watch the signs listed above. If any of them escalate overnight, escalate too. When in doubt, start the chat.",
    ),
    "SAFE AT HOME": (
        "Is this actually an emergency?",
        "Most of the time, no — this category is usually safe to manage at home with monitoring. "
        "That said, every critter is different. Run your specifics through the chat above for a read on your pet, not the average.",
    ),
}


def _build_faqs(topic):
    """Return a list of (question, answer) tuples for this topic."""
    if topic.faqs:
        return list(topic.faqs)
    faqs = []
    # 1) Lean-specific question
    lean_q = _LEAN_FAQ_BASE.get(topic.lean)
    if lean_q:
        faqs.append(lean_q)
    # 2) Watch-for question
    if topic.watch_for:
        signs = "; ".join(topic.watch_for[:3])
        faqs.append((
            "What symptoms should I watch for?",
            f"Three signs worth watching in the next 24\u201348 hours: {signs}. "
            "If any of them show up or get worse, move up one tier (home \u2192 vet, vet \u2192 ER).",
        ))
    # 3) Vet visit cost + teletriage hook
    faqs.append((
        "Do I need to pay for a vet visit just to ask?",
        "No. Our triage chat is free \u2014 it\'ll tell you whether a vet visit is actually warranted before you spend anything. "
        "If you do need a licensed vet, we connect you to one of our licensed partners in minutes, from your phone.",
    ))
    # 4) crittr pharmacy hook
    faqs.append((
        "Can crittr fill a prescription for this?",
        "If a licensed vet prescribes meds during or after triage, yes \u2014 Rx orders are routed through our licensed pharmacy partner. "
        "You can also browse our OTC picks directly; we only stock items our vet advisors actually recommend.",
    ))
    return faqs



# ---------------------------------------------------------------
# AI rec box — pick 3 OTC products relevant to each SEO topic
# ---------------------------------------------------------------
# Category-keyword map: (topic-keyword triggers) -> (product-text needles).
# Same pattern as anon_chat._picks_for_safe but tuned for SEO topics where
# we match against slug + title + watch_for strings.
_TOPIC_KEYWORD_MAP = [
    # Fleas, ticks, external parasites
    (("tick", "flea", "parasite", "mite", "scabies"),
     ("flea", "tick", "collar", "topical")),
    # Itch, skin, coat
    (("itch", "scratch", "allergy", "hot spot", "hot-spot", "coat", "shed",
      "rash", "dry skin", "dandruff", "ear-infection", "paws"),
     ("omega", "skin", "coat", "allergy")),
    # Joint, mobility, senior
    (("limp", "joint", "arthritis", "hip", "stair", "stiff", "senior",
      "walk", "mobility", "slow"),
     ("joint", "mobility")),
    # Anxiety, behavior
    (("anxiety", "scared", "storm", "firework", "separation", "bark",
      "nervous", "fear", "calm", "stress"),
     ("calm", "pheromone", "behavior", "anxiety")),
    # GI / digestive
    (("vomit", "diarrhea", "stool", "tummy", "gut", "eating", "appetite",
      "throwing-up", "throwing up", "grass", "stomach", "probiotic"),
     ("probiotic", "gut", "digestive")),
    # Dental
    (("breath", "tooth", "teeth", "dental", "plaque", "tartar", "gum",
      "drool", "mouth"),
     ("dental",)),
    # Multi / general wellness
    (("vitamin", "nutrient", "picky", "nutrition", "multivitamin"),
     ("multivitamin", "vitamin")),
]


def _picks_for_topic(q, topic) -> list:
    """Return up to 3 OTC products relevant to the given SEO topic.

    Pulls from products where requires_rx=FALSE AND amazon_url is set,
    keyword-scores each row against the topic's slug + title + watch_for,
    returns the top 3.  Falls back to 2 generic popular SKUs if nothing
    scores.
    """
    try:
        rows = q(
            "SELECT slug, public_name, public_blurb, price_cents, amazon_url, "
            "       species, tags, description, image_url "
            "FROM products "
            "WHERE in_stock = TRUE AND requires_rx = FALSE "
            "      AND amazon_url IS NOT NULL AND amazon_url <> ''"
        ) or []
    except Exception:
        return []
    if not rows:
        return []

    topic_text = " ".join([
        (topic.slug or ""),
        (topic.title or ""),
        (topic.question or ""),
        " ".join(topic.watch_for or []),
    ]).lower()

    def _score(row):
        score = 0
        text = " ".join(
            str(row.get(k) or "").lower()
            for k in ("public_name", "public_blurb", "description", "tags")
        )
        for triggers, needles in _TOPIC_KEYWORD_MAP:
            if any(t in topic_text for t in triggers):
                for n in needles:
                    if n in text:
                        score += 2
        # Species match small bonus
        species = str(row.get("species") or "").lower()
        if topic.species and topic.species in species:
            score += 1
        return score

    scored = [(_score(r), r) for r in rows]
    scored.sort(key=lambda t: -t[0])
    picks = [r for (s, r) in scored if s > 0][:3]
    if not picks:
        # Fallback: first 2 products so the panel never renders empty
        picks = rows[:2]
    return picks

# ---------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------
def register_seo_landings(app, q=None):
    """Wire GET /c/<slug>, GET /sitemap.xml.

    q: optional query helper; when provided, each SEO page renders an AI
    rec panel with 3 affiliate-linked products relevant to the topic.
    """
    @app.route("/c/<slug>")
    def seo_page(slug):
        topic = TOPICS.get(slug)
        if not topic:
            abort(404)
        picks = []
        if q is not None:
            try:
                picks = _picks_for_topic(q, topic)
            except Exception:
                picks = []
        return render_template_string(
            _HTML,
            topic=topic,
            all_topics=TOPICS,
            lean_class=_LEAN_CLASS.get(topic.lean, "safe"),
            faqs=_build_faqs(topic),
            picks=picks,
        )

    @app.route("/sitemap.xml")
    def sitemap():
        return Response(_sitemap_xml(), mimetype="application/xml")
