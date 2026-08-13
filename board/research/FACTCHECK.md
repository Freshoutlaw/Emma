# Fact-check report — Board of Advisors (Tier 2, stage two)

Second pass, run as an independent check that **assumed every file was
wrong**. For each entry: does the source exist, does it actually say this,
is it *this person's* idea (not one they credited away), and is the framing
honest (a narrow finding presented as a general law is a defect even when
every word is true)?

Method: web verification of each source against primary material
(kalzumeus.com archive, Stratechery pages, book chapter lists, and the
books' own public summaries). Where an entry could not be verified, it was
**rejected or replaced** — nothing was kept on plausibility.

## What the pass threw out (the discipline, in public)

1. **"Churn is a product problem" (attributed to patio11).** This is a
   widely-shared line, but an adversarial search found **no primary source**
   with that wording. It is the kind of quote that travels by blogs quoting
   each other. **Rejected** — it is not in the dossier.
2. **"Pricing as a marketing tool" (patio11).** Could not be verified as an
   article title. **Replaced** with two archive-verified essays: "You Can
   Probably Stand To Charge More" (2006-08-14) and "Doubling SaaS Revenue By
   Changing The Pricing Model" (2012-08-13).
3. **"The Stack Fallacy" (Thompson).** Could not verify the article URL or
   date in this pass. **Replaced** with "The Great Unbundling" (2017) and
   "Why Disney and ESPN Will Be OK" (2015-08-05), both verified against
   Stratechery's own pages.
4. **"Interruption is the enemy of productivity" (Fried).** Considered, but
   the exact chapter title could not be confirmed from the chapter lists
   reviewed. **Dropped** rather than risked.
5. **Rework "Embrace constraints" (Fried).** Not confirmed in the chapter
   lists actually reviewed. **Dropped**; used only chapters confirmed across
   multiple independent chapter summaries.

## Per seat

### Alex Hormozi — Offers & Growth
| Entry | Verdict | Notes |
|---|---|---|
| D1 Value Equation | **Confirmed** | Formula corroborated across independent book summaries and the audiobook's own section listing. |
| D2 Grand Slam Offer | **Confirmed** | Value + anchor + guarantee + scarcity + urgency + CTA documented consistently across summaries of the book. |
| D3 The offer is the business | **Confirmed** | The book's stated thesis (opening argument); corroborated by multiple reviews. |
| D4 Dream outcome | **Confirmed** | Corroborated including a direct book summary ("the thing people buy is the long-term value, aka their dream outcome"). |
| D5 Price anchoring | **Confirmed** | Documented as a Grand Slam component; framed as such (not as a standalone universal law). |
| D6 Marketing is math | **Confirmed** | Hormozi's own content repeats the framing; tied to the $100M Leads money model. |

*Framing correction applied:* D6 originally risked being presented as a
general law; it is Hormozi's acquisition framing and is worded as his
framework, not an axiom.

### Patrick McKenzie (patio11) — B2B Software & Pricing
| Entry | Verdict | Notes |
|---|---|---|
| D1 Charge more | **Confirmed** | Archive-verified post (2006-08-14). |
| D2 Charge a portion of value | **Confirmed** | Verified against the TwilioCon 2012 talk page, including the "$200/month, worth 10x" anecdote. |
| D3 Charge businesses | **Confirmed** | Verified against "Marketing For People Who Would Rather Be Building Stuff" (2013-04-24). |
| D4 Pricing model lever | **Confirmed** | Archive-verified (2012-08-13). |
| D5 Salary negotiation | **Confirmed** | Archive-verified (2012-01-23). |
| D6 Don't call yourself a programmer | **Confirmed** | Archive-verified (2011-10-28). |

### Jason Fried — Operations & Sustainable Growth
| Entry | Verdict | Notes |
|---|---|---|
| D1 Meetings are toxic | **Confirmed** | Chapter verified across multiple independent Rework chapter lists. |
| D2 Say no by default | **Confirmed** | Same. |
| D3 Underdo your competition | **Confirmed** | Same. |
| D4 Good enough is fine | **Confirmed** | Same. |
| D5 Let your customers outgrow you | **Confirmed** | Same. |
| D6 Calm company | **Confirmed** | Book thesis verified ("It Doesn't Have to Be Crazy at Work", 2018). Framing noted as conditional on a profitable, mature company — the entry says so. |

### Ben Thompson — Strategy & Technology
| Entry | Verdict | Notes |
|---|---|---|
| D1 Aggregation Theory | **Confirmed** | stratechery.com/2015/aggregation-theory/ (July 2015) verified. |
| D2 Modularizing suppliers | **Confirmed** | Verified against "Why Disney and ESPN Will Be OK" (2015-08-05), which restates the theory. |
| D3 The Great Unbundling | **Confirmed** | Referenced by name in Stratechery's own "Disney and Fox" (2017); the customer-relationship thesis matches Thompson's published argument. |
| D4 Moat = direct relationship | **Confirmed** | Direct corollary of the 2015 theory; framed as implication, not a separate article. |
| D5 Aggregator vs supplier | **Confirmed** | Direct corollary of the theory; framed as such. |
| D6 Structure explains the scoreboard | **Confirmed** | Verified against the Disney/ESPN piece's argument about content quality vs distribution. |

*Honesty note:* D4–D6 are worded as *implications of* Aggregation Theory
rather than as claims of separate articles. They are honest framing, not
fabricated citations.

## Machine pass (re-runnable)

The per-seat reports above were last re-verified by the independent
adversarial pass (`python -m board.research.verify`, model
gemma4:31b-cloud): **24/24 entries confirmed**, after one correction it
caught — Hormozi D4 cited an unverifiable "2026 audiobook" detail, which
was removed from the dossier and re-checked clean.

Machine reports (fresh per seat, re-runnable anytime):
`FACTCHECK_hormozi.md`, `FACTCHECK_patio11.md`, `FACTCHECK_fried.md`,
`FACTCHECK_thompson.md`. The pass is a separate model call from the
researcher's, sees only the dossier, and is instructed to assume the file
is wrong. Any entry it flags `corrected` or `rejected` is not trusted as
documented fact until the dossier is fixed and re-checked.

## Standing limits of the gate

Every entry here survived the adversarial pass **as of this date**. The
gate can prove a citation exists in the file; it can never prove the file
is true forever. Two things to know:

- Prices, platforms, and companies cited in doctrine will age. Re-run this
  pass when a seat is asked about a market that moved.
- Any operator edit drops an entry to `user` automatically — the fact-check
  no longer covers what it now says, and the chair is told.
