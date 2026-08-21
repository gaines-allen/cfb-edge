#!/usr/bin/env python3
"""
Open every cited source and check the claim is actually on the page.

Until this existed, a source was checked for a url starting with http and a
date inside 14 days, and nothing opened it. An agent could paraphrase a
coach, misread a date, or cite a page that says the opposite, and the card
passed every rule cleanly. The whole product is the judgment, so a judgment
resting on an unread source is the one thing that cannot be allowed to look
identical to a judgment resting on a read one.

Each source carries a quote, which is the exact sentence from the page that
supports the claim. This fetches the page, strips the markup, and checks the
quote is really there. What it cannot check is whether the page is honest,
whether the quote is in context, or whether the outlet is any good. Those
stay human, and the report says so rather than implying otherwise.

    python3 scripts/verify_sources.py --file picks_draft.json
    python3 scripts/verify_sources.py --file picks_draft.json --json

Exit 0 when every live pick rests on at least 1 verified source, 1 when any
does not.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402

from lib.card_rules import LIVE_THRESHOLD  # noqa: E402
from lib.runlog import RunLog  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# A quote shorter than this matches by accident. "he is out" appears on half
# the internet, so it proves nothing about this page.
MIN_QUOTE_CHARS = 25

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_MARKUP = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def page_text(body: str) -> str:
    body = _TAG.sub(" ", body)
    body = _MARKUP.sub(" ", body)
    return html.unescape(body)


def normalize(text: str) -> str:
    """
    Fold the 2 things that break an honest quote match: curly punctuation
    that a site renders differently from how it was copied, and whitespace.
    """
    text = unicodedata.normalize("NFKD", text)
    for fancy, plain in (("‘", "'"), ("’", "'"), ("“", '"'),
                         ("”", '"'), ("–", "-"), ("—", "-"),
                         (" ", " ")):
        text = text.replace(fancy, plain)
    return _WS.sub(" ", text).strip().lower()


def fetch(url: str, timeout: int = 20) -> tuple[str | None, str]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
    except requests.RequestException as e:
        return None, f"unreachable: {type(e).__name__}"
    if r.status_code >= 400:
        return None, f"http {r.status_code}"
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return None, f"not a readable page: {ctype or 'unknown content type'}"
    return r.text, f"http {r.status_code}"


def check_source(src: dict, fetcher=fetch) -> dict:
    """
    One source. status is verified, quote_not_found, no_quote, quote_too_short
    or unreachable. Only verified counts toward a live pick.
    """
    url = str(src.get("url", ""))
    quote = str(src.get("quote") or "").strip()
    out = {"url": url, "publisher": src.get("publisher"),
           "date": src.get("date"), "claim": src.get("claim"),
           "quote": quote}

    if not url.startswith("http"):
        return {**out, "status": "unreachable", "detail": "no usable url"}
    if not quote:
        return {**out, "status": "no_quote",
                "detail": "no quote given, so nothing could be checked "
                          "against the page"}
    if len(quote) < MIN_QUOTE_CHARS:
        return {**out, "status": "quote_too_short",
                "detail": f"{len(quote)} characters, under the "
                          f"{MIN_QUOTE_CHARS} needed to mean anything"}

    body, detail = fetcher(url)
    if body is None:
        return {**out, "status": "unreachable", "detail": detail}

    if normalize(quote) in normalize(page_text(body)):
        return {**out, "status": "verified", "detail": detail}
    return {**out, "status": "quote_not_found",
            "detail": f"the page loaded ({detail}) but does not contain "
                      f"that sentence"}


def verify(picks: list[dict], fetcher=fetch, log: RunLog | None = None) -> dict:
    rows, problems = [], []
    for i, p in enumerate(picks):
        try:
            conf = float(p.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        live = conf >= LIVE_THRESHOLD
        checked = [check_source(s, fetcher) for s in (p.get("sources") or [])
                   if isinstance(s, dict)]
        verified = [c for c in checked if c["status"] == "verified"]
        label = f"{p.get('matchup', '?')} {p.get('side', '?')}"

        rows.append({
            "index": i, "matchup": p.get("matchup"), "side": p.get("side"),
            "confidence": conf, "live": live,
            "sources": checked,
            "verified": len(verified), "total": len(checked),
        })
        if log:
            log.rows("sources", len(checked), matchup=p.get("matchup"),
                     verified=len(verified), live=live)

        if live and not verified:
            problems.append(
                f"{label} is published at {conf} and not 1 of its "
                f"{len(checked)} sources could be confirmed to say what the "
                f"rationale claims. "
                + "; ".join(f"{c['url']}: {c['status']}" for c in checked))
        for c in checked:
            if live and c["status"] != "verified":
                problems.append(
                    f"{label}: {c['url']} came back {c['status']} "
                    f"({c['detail']}). Read it yourself before trusting the "
                    f"claim it supports.")

    live_rows = [r for r in rows if r["live"]]
    return {
        "picks": len(rows),
        "live": len(live_rows),
        "sources_checked": sum(r["total"] for r in rows),
        "sources_verified": sum(r["verified"] for r in rows),
        "unverified_on_live_picks": problems,
        "passed": not any(r["live"] and r["verified"] == 0 for r in rows),
        "detail": rows,
        "note": ("A verified source means the quoted sentence really is on "
                 "that page. It does not mean the page is accurate, that the "
                 "quote is in context, or that the outlet is credible. Those "
                 "are still yours to judge."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="write the annotated report here")
    ap.add_argument("--dry-run", action="store_true",
                    help="accepted for symmetry, this script never writes "
                         "unless --out is given")
    args = ap.parse_args()

    log = RunLog("verify_sources", dry_run=args.dry_run)
    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2
    try:
        picks = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: {path} is not valid JSON: {e}", file=sys.stderr)
        return 2

    report = verify(picks, log=log)

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['sources_verified']} of {report['sources_checked']} "
              f"sources confirmed on the page.")
        for line in report["unverified_on_live_picks"]:
            print(f"UNVERIFIED: {line}", file=sys.stderr)
        if report["passed"] and not report["unverified_on_live_picks"]:
            print("Every live pick rests on a source that says what it claims.")

    log.finish(status="ok" if report["passed"] else "unverified")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
