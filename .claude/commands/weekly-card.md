---
description: Wednesday. Pull the board, build the slate, make the six picks, publish.
---

Run the full Wednesday sequence. Use the four agents, in this order, and do
not let any of them do another's job.

First, hand off to odds-scout. It pulls the FanDuel board and reports what has
moved since the last look, which numbers have crossed 3 or 7, and how many
Odds API credits are left. Wait for its report before going further.

Second, hand off to handicapper. It reads data/memory.json before anything
else, builds the slate, does the research the ratings model cannot do, and
writes the card. Six live plays is the target, above 8.0 is the bar, and four
good plays beats six padded ones. It logs everything it rated 6.0 and up so
the shadow picks are there to test the threshold.

Third, hand off to site-builder. It rebuilds site/index.html from the ledger.

Then commit and push, so the daily workflow picks it up from there.

Report back with the card as it would be entered at the book, the total units
at risk, and one line on anything odds-scout flagged that the handicapper
chose to ignore.

$ARGUMENTS
