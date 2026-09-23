"""
Shared estate/facilities-works exclusion, used by every source whose
defence-relevance signal is the buying organisation rather than a
category code.

Extracted from the CPPP (India) normalizer once CanadaBuys needed the
same logic: Canada's Department of National Defence publishes "Open
Construction Source List for CFB Halifax" for exactly the same reason
India's Military Engineer Services publishes sewage-line repairs —
armed forces maintain large estates, and that estate work is not the
capability procurement this platform matches products against.

Nothing here does I/O; it is pure string classification, which is what
makes it cheap to unit-test against real observed titles.
"""

import re
from typing import Optional


# ---------------------------------------------------------------
# Estate/facilities works exclusion.
#
# The organisation filter alone lets through a flood of Military
# Engineer Services estate work — sewage lines, toilet repairs,
# compound walls, sheds at a play school. MES is the Army's works
# and estates branch, so it publishes constantly, and none of it is
# defence CAPABILITY procurement of the kind this platform matches
# products against. Real observed titles that motivated this list:
#   "REPAIR/CLEANING OF EXISTING SEWAGE LINE, SOIL WASTE PIPES"
#   "SPECIAL REPAIR TO ROOF AND SUNKEN TREATMENT OF TOILETS"
#   "PROVN OF SHED AT BLOOMING BUDS PLAY SCHOOL"
#   "CERTAIN REPAIRS TO COMPOUND WALL, GATE / GRILL FENCING"
# ---------------------------------------------------------------
FACILITIES_WORKS_TERMS = (
    # sanitation / water
    "SEWAGE", "SOIL WASTE", "TOILET", "SANITARY", "PLUMBING", "DRAIN",
    "SEPTIC", "SOAK WELL", "MANHOLE", "WATER SUPPLY", "WATER TANK",
    "WATER STORAGE", "RESERVIOR", "RESERVOIR", "BORE WELL", "TUBEWELL", "STP",
    # structure / envelope
    "COMPOUND WALL", "BOUNDARY WALL", "BRICK WALL", "FENCING", "GRILL",
    "ROOF", "CEILING", "SHED", "FLOORING", "PORCH", "BALCONY", "CHAJJA",
    "RCC", "JOINERY", "DOOR", "WINDOW", "VENTILATOR", "CULVERT",
    "HARDSTANDING", "HARD STANDING", "CIVIL WORK", "BUILDING", "BLDG",
    # Canada: DND publishes "Open Construction Source List for CFB
    # Halifax" and similar for every base. Deliberately the full
    # phrase and not a bare "CONSTRUCTION" — Canada also tenders
    # "Construction Engineering Equipment" and spares for it, which
    # ARE materiel and must survive this filter.
    "CONSTRUCTION SOURCE LIST", "CONSTRUCTION SERVICES",
    # finishes / fittings
    "WHITEWASH", "WHITE WASH", "PAINTING", "POLISHING", "DISTEMPER",
    "FURNITURE", "CURTAIN", "UPHOLSTERY", "SIGN SAFETY", "SIGN BOARD",
    # grounds
    "HORTICULTURE", "GARDEN", "LAWN", "PLANTATION", "CAMPING GROUND",
    "PLAY SCHOOL", "HOCKEY", "PLAYGROUND", "SPORTS", "CATTLE",
    # services / estate ops
    "HOUSEKEEPING", "CLEANING", "SWEEPING", "SCAVENGING", "ARTIFICER",
    "PETTY REPAIR", "TERM CONTRACT", "DAY TO DAY", "CONSULTANCY",
    "SOIL INVEST", "DESIGN DRAWING", "DETAILED ENGINEERING",
    # building services / utilities
    "LIGHT FITTING", "LED LIGHT", "STREET LIGHT", "HIGH MAST", "WIRING",
    "TRANSFORMER", "CONDUCTOR", "SUB STATION", "SUBSTATION", "KV ",
    "FIRE ALARM", "FIRE FIGHTING", "CRANE", "LIFT", "BIO-GAS", "BIOGAS",
    # accommodation
    "QUARTER", "QTR", "BARRACK", "MESS ", "KITCHEN", "TENT", "ACCN",
    "PARKING", "STORAGE ACCN", "OTM",
    # MES works vocabulary — these prefixes are near-certain estate work
    "PROVN OF", "ADDN", "ALTN", "SPECIAL REPAIR", "SPL REPAIR",
    "REPAIR AND MAINT", "REPAIR/MAINT", "REPAIR / MAINT", "MAINT REPAIR",
    "ANNUAL REPAIR", "REPAIR REPLACEMENT", "REPAIR/REPLACEMENT",
    "REPAIRS/ REPLACEMENT", "REPAIR TO", "REPAIRS TO", "REPAIR/RENEWAL",
    "REPAIR/CLEANING", "REPAIR CLEANING", "IMPROVEMENT OF",
)

# If a title mentions real defence materiel, keep it even when it
# also contains a works term. "Repair and maintenance services of
# warships" is genuine defence capability procurement — it is its own
# CPV code (5064) in the UK/EU sources — and must not be discarded
# just because it says "repair".
# Matched on word boundaries, never as bare substrings. Learned from
# real leakage: "TANK" rescued "SEPTIC TANK" and "WATER TANK", and
# "ENGINE" rescued "Detailed ENGINEERING" — both estate works that
# then sailed past the facilities filter. "TANK" is deliberately
# absent for that reason; armoured vehicles are caught by ARMOURED /
# ARMORED instead, which carry no such ambiguity.
DEFENCE_MATERIEL_TERMS = (
    "WARSHIP", "WARSHIPS", "SUBMARINE", "SUBMARINES", "AIRCRAFT", "HELICOPTER",
    "MISSILE", "MISSILES", "AMMUNITION", "ORDNANCE", "WEAPON", "WEAPONS",
    "ARMAMENT", "ARMAMENTS", "ARTILLERY", "RADAR", "SONAR", "AVIONICS",
    "UAV", "DRONE", "DRONES", "SIMULATOR", "ARMOURED", "ARMORED",
    "ENCRYPTION", "OPTRONIC", "OPTRONICS", "PROPELLANT", "EXPLOSIVE",
    "EXPLOSIVES", "TORPEDO", "TORPEDOES", "AIRFRAME", "AERO ENGINE",
    "ELECTRONIC WARFARE", "NIGHT VISION", "COMMUNICATION EQUIPMENT",
)

_MATERIEL_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(re.escape(t) for t in DEFENCE_MATERIEL_TERMS) + r")(?![A-Za-z])",
    re.IGNORECASE,
)


def is_uninformative_title(title: Optional[str]) -> bool:
    """
    True for titles that are nothing but an internal reference code —
    real examples: 'AGE(I)(U)B/R-TOKEN-25/2026-27'. They carry no
    describable subject at all, so they can never keyword-match a
    capability and would only ever surface as noise. Detected
    structurally (no two ordinary words) rather than by listing
    individual code formats.
    """
    if not title:
        return True
    return not re.search(r"[A-Za-z]{4,}\s+[A-Za-z]{3,}", title)


def is_facilities_works(title: Optional[str]) -> bool:
    """
    True when a tender title looks like estate/facilities work rather
    than defence capability procurement. Pure and unit-tested — this
    is the filter that keeps MES's constant stream of building
    maintenance out of the opportunity pipeline.
    """
    if not title:
        return False
    if _MATERIEL_RE.search(title):
        return False  # real materiel — keep it even if worded as a repair
    upper = title.upper()
    return any(term in upper for term in FACILITIES_WORKS_TERMS)
