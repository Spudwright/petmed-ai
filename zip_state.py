"""crittr.ai — ZIP code to state, offline.

WHY THIS IS A LOCAL TABLE AND NOT AN API CALL. The state decides compliance: whether we may
route a case to a vet at all. That decision must not depend on a third-party geocoder being
up, being paid for, or returning something unexpected — if Google is down we should still
know that 87507 is New Mexico, and we should get the same answer every time.

The mapping is deterministic: US ZIP prefixes were allocated by state and the first three
digits identify one. This table is the standard allocation. It is used ONLY to determine
the state; latitude and longitude for "vets near me" still come from the geocoder, because
being wrong about a distance is cosmetic and being wrong about a state is not.

ALSO THE THING THAT MAKES ROUTING WORK AT ALL. The triage hook needs a state and the
browser only offers coordinates — which many owners decline to share. A ZIP is easier to
type at 2am than it is to grant location permission, and it is the only input that produces
the state the compliance gate requires.
"""
import re

# (low_prefix, high_prefix, state) over the first three ZIP digits.
_RANGES = [
    (5, 5, "NY"), (6, 9, "PR"), (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"),
    (39, 49, "ME"), (50, 59, "VT"), (60, 69, "CT"), (70, 89, "NJ"), (100, 149, "NY"),
    (150, 196, "PA"), (197, 199, "DE"), (200, 200, "DC"), (201, 201, "VA"),
    (202, 205, "DC"), (206, 219, "MD"), (220, 246, "VA"), (247, 268, "WV"),
    (270, 289, "NC"), (290, 299, "SC"), (300, 319, "GA"), (320, 349, "FL"),
    (350, 369, "AL"), (370, 385, "TN"), (386, 397, "MS"), (398, 399, "GA"),
    (400, 427, "KY"), (430, 459, "OH"), (460, 479, "IN"), (480, 499, "MI"),
    (500, 528, "IA"), (530, 549, "WI"), (550, 567, "MN"), (570, 577, "SD"),
    (580, 588, "ND"), (590, 599, "MT"), (600, 629, "IL"), (630, 658, "MO"),
    (660, 679, "KS"), (680, 693, "NE"), (700, 714, "LA"), (716, 729, "AR"),
    (730, 732, "OK"), (733, 733, "TX"),          # 733 is Austin, not Oklahoma
    (734, 749, "OK"), (750, 799, "TX"),
    (800, 816, "CO"), (820, 831, "WY"), (832, 838, "ID"), (840, 847, "UT"),
    (850, 865, "AZ"), (870, 884, "NM"),
    (885, 885, "TX"),                            # El Paso sits in the NM block
    (889, 898, "NV"), (900, 961, "CA"), (967, 968, "HI"), (970, 979, "OR"),
    (980, 994, "WA"), (995, 999, "AK"),
]

_ZIP_RE = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")


def normalise(zip_code):
    """A clean 5-digit ZIP, or None. Accepts ZIP+4 and stray whitespace."""
    m = _ZIP_RE.match(str(zip_code or ""))
    return m.group(1) if m else None


def state_for_zip(zip_code):
    """(state|None, reason). None is an honest answer and must stay one.

    Returning None means the compliance gate refuses to route, which is the correct
    outcome for a ZIP we cannot place — better than guessing a state and routing a case
    to a vet who is not licensed for it.
    """
    z = normalise(zip_code)
    if not z:
        return None, "that doesn't look like a 5-digit ZIP code"
    p = int(z[:3])
    for lo, hi, st in _RANGES:
        if lo <= p <= hi:
            return st, ""
    return None, f"we can't place ZIP {z} — it may not be a US postal code"
