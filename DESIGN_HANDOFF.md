# Steve UX handoff

This repo is the shared source for Codex and Claude. Change the generator in `scripts/build_site.py`, then rebuild `site/index.html` with `.venv/bin/python scripts/build_site.py`. Do not hand-edit betting data or the generated page.

The design rule is: the data obeys the grid; Steve does not. The page should feel like a college football magazine left at the end of a bar. Data rows stay orderly. Steve can interrupt from the margin, tilt a note, break a section width, or shout a heading.

The first redesign pass changed the masthead, sticky section navigation, section names, page widths, pick tickets, short-list rows, TV wall, receipts, spacing, and Steve's microcopy. The source-of-truth files are `scripts/build_site.py` and `site/steve.png`. `site/index.html` and `site/data.json` are generated outputs.

Before changing anything, run `.venv/bin/python scripts/selftest.py`. After a visual change, rebuild the site and run that command again. The betting ledger, prices, results, confidence values, rationales, and source links must remain untouched.
