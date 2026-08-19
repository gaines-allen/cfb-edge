"""
Team name matching between The Odds API, CollegeFootballData, and ESPN.

The three sources spell schools differently and none of them publishes a
mapping. A name that fails to match is not a cosmetic problem: an unmatched
game gets projected against the wrong ratings, or a placed pick sits pending
forever and never settles.

The big one, found the first time a real board came back: The Odds API
returns schools with the mascot attached. "Alabama Crimson Tide", "TCU
Horned Frogs", "Arizona State Sun Devils". CFBD returns "Alabama", "TCU",
"Arizona State". Stripping the last word does not work, because mascots run
one to three words and "Arizona Wildcats" would become "Arizona" while
"Arizona State Sun Devils" would become "Arizona State Sun". So the mascot
map in data/team_aliases.json is generated from ESPN's team list, which
publishes location and mascot as separate fields for every school.

The rule here is that this module never guesses. Normalization, the mascot
map, and an explicit variant table. Anything that falls through comes back
as None so the caller can shout about it. An earlier version of the slate
builder used a six character prefix fallback, which is exactly the kind of
thing that quietly maps Ohio onto Ohio State on the one week it matters.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

ALIAS_FILE = Path(__file__).resolve().parents[2] / "data" / "team_aliases.json"

# Schools whose names diverge across sources. Key is the canonical CFBD
# spelling, value is every other spelling seen in the wild. Anything not
# listed here is expected to match on normalization alone.
VARIANTS: dict[str, list[str]] = {
    "Mississippi": ["Ole Miss", "Ole Miss Rebels"],
    "Southern Mississippi": ["Southern Miss", "So Miss", "USM",
                             "Southern Mississippi Golden Eagles",
                             "Southern Miss Golden Eagles"],
    "Miami": ["Miami (FL)", "Miami FL", "Miami Florida", "Miami Hurricanes",
              "Miami-Florida", "Miami (FL) Hurricanes", "Miami FL Hurricanes"],
    "Miami (OH)": ["Miami OH", "Miami Ohio", "Miami-Ohio", "Miami RedHawks",
                   "Miami (Ohio)"],
    "Louisiana": ["Louisiana-Lafayette", "UL Lafayette", "Louisiana Lafayette",
                  "Ragin Cajuns", "Louisiana Ragin' Cajuns"],
    "Louisiana Monroe": ["Louisiana-Monroe", "UL Monroe", "ULM"],
    "Louisiana Tech": ["LA Tech"],
    "LSU": ["Louisiana State", "Louisiana St"],
    "UT San Antonio": ["UTSA", "Texas-San Antonio", "Texas San Antonio"],
    "UTEP": ["Texas-El Paso", "Texas El Paso"],
    "UAB": ["Alabama-Birmingham", "Alabama Birmingham"],
    "UCF": ["Central Florida"],
    "South Florida": ["USF"],
    "SMU": ["Southern Methodist"],
    "TCU": ["Texas Christian"],
    "BYU": ["Brigham Young"],
    "Pittsburgh": ["Pitt"],
    "Appalachian State": ["App State", "App St", "App State Mountaineers",
                          "Appalachian State Mountaineers"],
    "NC State": ["North Carolina State", "North Carolina St", "N.C. State",
                 "NC St"],
    "North Carolina": ["UNC"],
    "Hawai'i": ["Hawaii", "Hawai i"],
    "San José State": ["San Jose State", "San Jose St", "SJSU"],
    "Massachusetts": ["UMass", "U Mass"],
    "Connecticut": ["UConn", "U Conn"],
    "Florida International": ["FIU"],
    "Florida Atlantic": ["FAU"],
    "Sam Houston": ["Sam Houston State", "Sam Houston St",
                    "Sam Houston State Bearkats", "Sam Houston Bearkats"],
    "Central Michigan": ["Central Mich"],
    "Western Michigan": ["Western Mich"],
    "Eastern Michigan": ["Eastern Mich"],
    "Western Kentucky": ["WKU"],
    "Middle Tennessee": ["Middle Tennessee State", "MTSU", "Middle Tenn"],
    "Army": ["Army West Point", "Army Black Knights"],
    "Navy": ["Navy Midshipmen"],
    "Nevada": ["Nevada Reno"],
    "UNLV": ["Nevada-Las Vegas", "Nevada Las Vegas"],
    "Texas A&M": ["Texas AandM", "Texas A and M"],
    "Jacksonville State": ["Jax State", "Jacksonville St"],
    "Kennesaw State": ["Kennesaw St"],
    "Coastal Carolina": ["Coastal Car"],
    "Georgia Southern": ["Ga Southern"],
    "Georgia State": ["Ga State"],
    "James Madison": ["JMU"],
    "Southern Illinois": ["Southern Ill"],
    "Charlotte": ["North Carolina-Charlotte", "UNC Charlotte"],
    "Ohio": ["Ohio Bobcats"],
    # Spellings a real FanDuel board returned that the ESPN mascot map does
    # not cover, because ESPN lists the school under a different name.
    "Albany": ["UAlbany", "Albany Great Danes", "UAlbany Great Danes"],
    "The Citadel": ["Citadel", "Citadel Bulldogs", "The Citadel Bulldogs"],
    "Houston Christian": ["Houston Baptist", "Houston Baptist Huskies",
                          "Houston Christian Huskies"],
    "Long Island University": ["LIU", "LIU Sharks",
                               "Long Island University Sharks"],
    "Nicholls": ["Nicholls State", "Nicholls State Colonels",
                 "Nicholls Colonels"],
    "Southeastern Louisiana": ["SE Louisiana", "Southeastern Louisiana Lions",
                               "SE Louisiana Lions"],
    "UT Rio Grande Valley": ["UTRGV", "UT Rio Grande Valley Vaqueros",
                             "UTRGV Vaqueros"],
}

# Pairs that must never collapse into each other. If normalization ever
# maps both members of one of these pairs to the same key, the selftest
# fails rather than letting it reach a live slate.
MUST_STAY_DISTINCT: list[tuple[str, str]] = [
    ("Ohio", "Ohio State"),
    ("Miami", "Miami (OH)"),
    ("Washington", "Washington State"),
    ("Oregon", "Oregon State"),
    ("Michigan", "Michigan State"),
    ("Arizona", "Arizona State"),
    ("Oklahoma", "Oklahoma State"),
    ("Mississippi", "Mississippi State"),
    ("Louisiana", "Louisiana Monroe"),
    ("Louisiana", "Louisiana Tech"),
    ("Kansas", "Kansas State"),
    ("Iowa", "Iowa State"),
    ("Colorado", "Colorado State"),
    ("Utah", "Utah State"),
    ("San Diego State", "San José State"),
    ("Texas", "Texas State"),
    ("Texas", "Texas Tech"),
    ("Texas", "Texas A&M"),
    ("Florida", "Florida State"),
    ("Georgia", "Georgia State"),
    ("Georgia", "Georgia Tech"),
    ("Alabama", "Alabama State"),
    ("Arkansas", "Arkansas State"),
    ("Boston College", "Boston University"),
    ("Northwestern", "Northwestern State"),
    ("Carolina", "South Carolina"),
    ("North Carolina", "NC State"),
]

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")

# Words that carry no distinguishing information once the rest is normalized.
_DROP = {"university", "the", "of"}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def normalize(name: str) -> str:
    """
    Fold a school name to a comparison key.

    Handles accents, punctuation, ampersands, and the St / St. / State
    abbreviation. Deliberately does not handle anything clever, because
    clever is how Ohio becomes Ohio State.
    """
    if not name:
        return ""
    # Apostrophes are deleted rather than turned into spaces, so Hawai'i
    # folds to hawaii instead of "hawai i".
    s = str(name).replace("'", "").replace("’", "")
    s = strip_accents(s).lower()
    s = s.replace("&", " and ")
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()

    tokens = [t for t in s.split() if t not in _DROP]
    # Any standalone "St" token becomes "state". No FBS school is a Saint,
    # and sources abbreviate mid-string too ("Youngstown St Penguins").
    return " ".join("state" if t == "st" else t for t in tokens)


def _load_mascot_map() -> dict[str, str]:
    """
    Normalized "school + mascot" to school, generated from ESPN's team list
    by scripts/build_aliases.py. Missing file is survivable: matching falls
    back to the variant table and normalization, and the caller still gets
    None rather than a guess.
    """
    try:
        return json.loads(ALIAS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


MASCOT_MAP = _load_mascot_map()


def _build_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for canonical, alts in VARIANTS.items():
        idx[normalize(canonical)] = canonical
        for a in alts:
            idx[normalize(a)] = canonical
    return idx


VARIANT_INDEX = _build_index()


def _build_groups() -> dict[str, list[str]]:
    """
    Normalized key to every spelling in its family, canonical first.

    This is what makes matching work in both directions. CFBD calls it
    "Southern Mississippi" and ESPN calls it "Southern Miss", so knowing
    which one is canonical is less useful than being able to try all of
    them against whatever the destination system actually uses.
    """
    groups: dict[str, list[str]] = {}
    for canonical, alts in VARIANTS.items():
        family = [canonical, *alts]
        for member in family:
            groups[normalize(member)] = family
    return groups


VARIANT_GROUPS = _build_groups()


def strip_mascot(name: str) -> str:
    """
    "Alabama Crimson Tide" -> "Alabama". Returns the input unchanged when
    the name is not in the mascot map, so callers can keep trying.
    """
    return MASCOT_MAP.get(normalize(name), name)


def canonical(name: str, known: set[str] | None = None) -> str | None:
    """
    Map a source's spelling onto the canonical one.

    known is the authoritative team set for the destination system, usually
    the keys of the CFBD rating book. Returns None when there is no
    confident match. None means stop and report, never substitute.
    """
    if not name:
        return None

    if known and name in known:
        return name

    # Candidates in order of trust: the name as given, then the mascot map,
    # then every spelling in the same variant family. Never anything fuzzy.
    key = normalize(name)
    candidates = [key]

    stripped = normalize(strip_mascot(name))
    if stripped not in candidates:
        candidates.append(stripped)

    # A mascot-stripped name may itself be a variant: "Ole Miss Rebels"
    # strips to "Ole Miss", which the family maps onto "Mississippi".
    for c in list(candidates):
        for member in VARIANT_GROUPS.get(c, []):
            nk = normalize(member)
            if nk not in candidates:
                candidates.append(nk)

    if not known:
        for c in candidates:
            if c in VARIANT_INDEX:
                return VARIANT_INDEX[c]
        return None

    by_key: dict[str, list[str]] = {}
    for k in known:
        by_key.setdefault(normalize(k), []).append(k)
        stripped = normalize(strip_mascot(k))
        if stripped != normalize(k):
            by_key.setdefault(stripped, []).append(k)

    for c in candidates:
        hits = by_key.get(c)
        if hits and len(set(hits)) == 1:
            return hits[0]
    return None


def suggest(name: str, known: set[str], limit: int = 3) -> list[str]:
    """
    Candidates for a human to look at after a match fails. Advisory only,
    nothing in the pipeline is allowed to act on this.
    """
    key = normalize(name)
    kt = set(key.split())
    scored = []
    for k in known:
        ot = set(normalize(k).split())
        if not kt or not ot:
            continue
        overlap = len(kt & ot) / len(kt | ot)
        if overlap > 0:
            scored.append((overlap, k))
    scored.sort(reverse=True)
    return [k for _, k in scored[:limit]]


def audit(names: list[str], known: set[str]) -> dict:
    """Match a batch and report what fell through, with suggestions."""
    matched, failed = {}, {}
    for n in names:
        c = canonical(n, known)
        if c:
            matched[n] = c
        else:
            failed[n] = suggest(n, known)
    return {
        "total": len(names),
        "matched": len(matched),
        "failed": len(failed),
        "mapping": matched,
        "unmatched": failed,
    }
