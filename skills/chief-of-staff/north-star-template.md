---
type: north-star
product: REPLACE_WITH_PRODUCT_NAME
status: active
target: "$REPLACE_AMOUNT in REPLACE_METRIC by REPLACE_DATE"
filter: [sales, delivery_speed, margin, retention]
last_reviewed: REPLACE_WITH_TODAYS_DATE_YYYY_MM_DD
review_cadence_days: 30
---
# REPLACE_WITH_PRODUCT_NAME — North Star
## ⚠️ Errata
(Leave empty until your doctrine drifts. When you make a mid-cycle decision that contradicts something below, write it here with a date. Errata is read first and treated as authoritative over the body.)
---
## Objective
One paragraph. What is this product trying to do, and what does winning look like inside the next 90 days? Be concrete. "Generate near-term cash" is concrete; "build a great product" is not.
## Operating Rules
Bulleted list of rules that constrain how you spend time and money. The brief skill will cite these directly when flagging anti-priorities. Examples:
- Lead with business value. Sell outcomes, not backend sophistication.
- Do not build ahead of the revenue loop.
- Build only what improves sales, delivery speed, margin, or retention within the next 90 days.
- Protect margin: simple external pricing, strict internal limits.
- Do not sell hours. Ever.
Rewrite these for your business. The point is to have rules concrete enough that a queued task can clearly violate one.
## Commercial Ladder
Optional. List the stages a customer moves through (e.g. Free Scan → Paid Audit → Recurring Reporting → Implementation). Used by the skill when reasoning about which stage today's work touches.
## Notes
Anything else doctrinal. Not used directly by the skill, but useful for the user re-reading the doc.
---
## How to use this template
1. Replace every `REPLACE_*` token in the frontmatter and body.
2. Save as `north-star.md` in your working directory, OR put it under your context tree and point `context/business/index.md` at it.
3. Update `last_reviewed` whenever you sit down and confirm "yes, this is still the right doctrine." The skill will warn you if it goes stale (default: 30 days).
4. Add an errata entry whenever you make a mid-cycle decision that overrides something here. Don't rewrite the body — that's what errata is for.
