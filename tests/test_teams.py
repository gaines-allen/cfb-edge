"""
Team name matching, and the guard that keeps it from guessing.

An unmatched pick never settles, sits pending forever, and silently
vanishes from the record, which is the most dangerous failure this system
has. A wrongly matched pick is worse still, because it settles against the
wrong game and the ledger looks fine.

So teams.py returns None on purpose. Every assertion here came out of
scripts/selftest.py, plus a source scan that fails if anyone reintroduces
the six character prefix fallback that once mapped Ohio onto Ohio State.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from lib import teams as teams_mod
from lib.teams import (
    MUST_STAY_DISTINCT, VARIANTS, canonical, normalize, suggest,
)

TEAMS_SRC = Path(teams_mod.__file__).read_text()


# --------------------------------------------------------- collisions

@pytest.mark.parametrize("a,b", MUST_STAY_DISTINCT,
                         ids=[f"{a}|{b}" for a, b in MUST_STAY_DISTINCT])
def test_must_stay_distinct(a, b):
    """The check that stops Ohio from being graded as Ohio State."""
    assert normalize(a) != normalize(b), (
        f"{a} and {b} both normalize to {normalize(a)}"
    )


def test_must_stay_distinct_covers_27_pairs():
    """The handoff records 27 near collision pairs. Losing one loses a guard."""
    assert len(MUST_STAY_DISTINCT) >= 27


# ----------------------------------------------------------- variants

VARIANT_CASES = [(alt, canon) for canon, alts in VARIANTS.items() for alt in alts]


@pytest.mark.parametrize("alt,canon", VARIANT_CASES,
                         ids=[f"{a}->{c}" for a, c in VARIANT_CASES])
def test_every_variant_resolves(alt, canon):
    assert canonical(alt, {canon}) == canon


def test_no_duplicate_keys_in_variants():
    """
    Python collapses a duplicate dict key at parse time, so a second entry
    for a school silently deletes the first one's spellings. That bug
    shipped once. It can only be caught by reading the source.
    """
    keys = re.findall(r'^\s{4}"([^"]+)":\s*\[', TEAMS_SRC, re.M)
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert dupes == []


# ---------------------------------------------------------- canonical

CFBD_SAMPLE = {
    "Mississippi", "Mississippi State", "Ohio", "Ohio State", "Miami",
    "Miami (OH)", "Washington", "Washington State", "San José State",
    "San Diego State", "Hawai'i", "UT San Antonio", "Appalachian State",
    "NC State", "North Carolina", "Louisiana", "Louisiana Monroe",
    "Louisiana Tech", "LSU", "Texas A&M", "Sam Houston", "Connecticut",
    "Massachusetts", "UCF", "South Florida", "Pittsburgh", "Army",
    "Southern Mississippi", "Michigan", "Michigan State",
}

CANONICAL_CASES = [
    ("Ole Miss", "Mississippi"), ("Mississippi State", "Mississippi State"),
    ("Ohio", "Ohio"), ("Ohio State", "Ohio State"),
    ("Miami", "Miami"), ("Miami (OH)", "Miami (OH)"), ("Miami Ohio", "Miami (OH)"),
    ("San Jose State", "San José State"), ("Hawaii", "Hawai'i"),
    ("UTSA", "UT San Antonio"), ("App State", "Appalachian State"),
    ("North Carolina State", "NC State"), ("UL Monroe", "Louisiana Monroe"),
    ("Louisiana-Lafayette", "Louisiana"), ("Louisiana State", "LSU"),
    ("Southern Miss", "Southern Mississippi"), ("Central Florida", "UCF"),
    ("USF", "South Florida"), ("Pitt", "Pittsburgh"), ("UConn", "Connecticut"),
    ("UMass", "Massachusetts"), ("Sam Houston State", "Sam Houston"),
    ("Army West Point", "Army"), ("Washington", "Washington"),
    ("Washington State", "Washington State"), ("Michigan", "Michigan"),
    ("Texas A and M", "Texas A&M"),
]


@pytest.mark.parametrize("src,want", CANONICAL_CASES,
                         ids=[s for s, _ in CANONICAL_CASES])
def test_canonical(src, want):
    assert canonical(src, CFBD_SAMPLE) == want


@pytest.mark.parametrize("bad", ["Nowhere Tech", "Fake State",
                                 "Springfield A&M", ""])
def test_unknown_refuses_to_match(bad):
    """
    Silence is the bug we are guarding against, so a plausible wrong answer
    is a failure and None is the correct result.
    """
    assert canonical(bad, CFBD_SAMPLE) is None


def test_suggest_returns_candidates():
    assert bool(suggest("Mississippi Stat", CFBD_SAMPLE))


# ------------------------------------------------------- board mascots

BOARD_SAMPLE = {
    "Alabama Crimson Tide": "Alabama",
    "TCU Horned Frogs": "TCU",
    "Arizona State Sun Devils": "Arizona State",
    "Arizona Wildcats": "Arizona",
    "Ohio State Buckeyes": "Ohio State",
    "Ohio Bobcats": "Ohio",
    "Miami (FL) Hurricanes": "Miami",
    "Miami (OH) RedHawks": "Miami (OH)",
    "Hawaii Rainbow Warriors": "Hawai'i",
    "Appalachian State Mountaineers": "Appalachian State",
    "Southern Mississippi Golden Eagles": "Southern Mississippi",
    "Sam Houston State Bearkats": "Sam Houston",
    "San Jose State Spartans": "San José State",
    "Ole Miss Rebels": "Mississippi",
    "UTSA Roadrunners": "UT San Antonio",
    "NC State Wolfpack": "NC State",
    "UMass Minutemen": "Massachusetts",
    "Texas A&M Aggies": "Texas A&M",
    "Louisiana Ragin Cajuns": "Louisiana",
    "Army Black Knights": "Army",
    "Washington State Cougars": "Washington State",
    "Washington Huskies": "Washington",
}
BOARD_KNOWN = set(BOARD_SAMPLE.values()) | {
    "Mississippi State", "Michigan", "Michigan State", "Louisiana Monroe",
}
BOARD_CASES = list(BOARD_SAMPLE.items())


@pytest.mark.parametrize("board_name,want", BOARD_CASES,
                         ids=[n for n, _ in BOARD_CASES])
def test_board_names_carry_mascots(board_name, want):
    """
    Names taken from a real FanDuel board, which returns the mascot
    attached. Stripping trailing words does not work, because mascots run 1
    to 3 of them and Arizona State Sun Devils would become Arizona State Sun.
    """
    assert canonical(board_name, BOARD_KNOWN) == want


def test_board_garbage_refuses_to_match():
    assert canonical("Nowhere Tech Fighting Nobodies", BOARD_KNOWN) is None


# ------------------------------------------------- the no-guessing rule

FUZZY_MARKERS = [
    "difflib", "SequenceMatcher", "get_close_matches", "fuzz",
    "levenshtein", "jaro", "startswith", "[:6]", "[:7]", "[:8]",
]


def strip_comments_and_strings(src: str) -> str:
    """
    Executable code only. teams.py carries a comment reading "Never anything
    fuzzy", and a guard that trips on the comment warning against the bug
    rather than on the bug is a guard nobody will keep.
    """
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return src
    return " ".join(out)


def test_matching_path_has_no_fuzzy_fallback():
    """
    MUST_STAY_DISTINCT exists because a 6 character prefix fallback once
    mapped Ohio onto Ohio State. This reads the source of the matching
    functions and fails if anything like that comes back. suggest() is
    excluded on purpose: it scores token overlap, but it is advisory only
    and nothing in the pipeline is allowed to act on it.
    """
    matching_src = "\n".join(
        inspect.getsource(fn) for fn in
        (teams_mod.canonical, teams_mod.normalize, teams_mod._build_index,
         teams_mod.strip_mascot)
    )
    code = strip_comments_and_strings(_dedent(matching_src))
    found = [m for m in FUZZY_MARKERS if m.lower() in code.lower()]
    assert found == [], (
        f"the matching path grew a fuzzy fallback: {found}. teams.py returns "
        f"None on purpose, because a plausible wrong answer settles against "
        f"the wrong game and the ledger still looks fine."
    )


def _dedent(src: str) -> str:
    from textwrap import dedent
    return dedent(src)


def test_module_does_not_import_difflib():
    assert "difflib" not in strip_comments_and_strings(TEAMS_SRC)


def test_suggest_is_advisory_only():
    """
    suggest() must never be wired into canonical(). If it were, every
    unmatched name would resolve to its nearest neighbour.
    """
    assert "suggest" not in strip_comments_and_strings(
        _dedent(inspect.getsource(teams_mod.canonical)))
