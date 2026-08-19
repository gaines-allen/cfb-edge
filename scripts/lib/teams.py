"""
Team name matching between The Odds API, CollegeFootballData, and ESPN.

The three sources spell schools differently and none of them publishes a
mapping. A name that fails to match is not a cosmetic problem: an unmatched
game gets projected against the wrong ratings, or a placed pick sits pending
forever and never settles.

The rule here is that this module never guesses. Normalization plus an
explicit variant table, and anything that falls through comes back as None
so the caller can shout about it. An earlier version of the slate builder
used a six character prefix fallback, which is exactly the kind of thing
that quietly maps Ohio onto Ohio State on the one week it matters.
"""

from __future__ import annotations

import re
import unicodedata

# Schools whose names diverge across sources. Key is the canonical CFBD
# spelling, value is every other spelling seen in the wild. Anything not
# listed here is expected to match on normalization alone.
VARIANTS: dict[str, list[str]] = {
    "Mississippi": ["Ole Miss", "Ole Miss Rebels"],
    "Southern Mississippi": ["Southern Miss", "So Miss", "USM"],
    "Miami": ["Miami (FL)", "Miami FL", "Miami Florida", "Miami Hurricanes",
              "Miami-Florida"],
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
    "Appalachian State": ["App State", "App St"],
    "NC State": ["North Carolina State", "North Carolina St", "N.C. State",
                 "NC St"],
    "North Carolina": ["UNC"],
    "Hawai'i": ["Hawaii", "Hawai i"],
    "San José State": ["San Jose State", "San Jose St", "SJSU"],
    "Massachusetts": ["UMass", "U Mass"],
    "Connecticut": ["UConn", "U Conn"],
    "Florida International": ["FIU"],
    "Florida Atlantic": ["FAU"],
    "Sam Houston": ["Sam Houston State", "Sam Houston St"],
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
    s = strip_accents(str(name)).lower()
    s = s.replace("&", " and ")
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()

    tokens = [t for t in s.split() if t not in _DROP]
    # "St" and "St." expand to "state" only in trailing position, so
    # "St. John's" style names are not mangled.
    if tokens and tokens[-1] == "st":
        tokens[-1] = "state"
    return " ".join(tokens)


def _build_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for canonical, alts in VARIANTS.items():
        idx[normalize(canonical)] = canonical
        for a in alts:
            idx[normalize(a)] = canonical
    return idx


VARIANT_INDEX = _build_index()


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

    key = normalize(name)

    if known:
        by_key = {}
        for k in known:
            by_key.setdefault(normalize(k), []).append(k)
        if key in by_key and len(by_key[key]) == 1:
            return by_key[key][0]

        mapped = VARIANT_INDEX.get(key)
        if mapped:
            if mapped in known:
                return mapped
            mk = normalize(mapped)
            if mk in by_key and len(by_key[mk]) == 1:
                return by_key[mk][0]
        return None

    return VARIANT_INDEX.get(key)


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
