---
name: chief-of-staff
description: The Chief of Staff routine for your AI agent. Use when the user asks to plan their day, requests a morning brief, or asks "what should I work on" — produces a doctrine-anchored daily prioritization grounded in the user's own revenue north star, with verbatim citations from their doctrine. Not a generic productivity tool. Hard-fails (gracefully) when no north-star doc exists.
---
# Chief of Staff
Give the agent the role a real Chief of Staff plays for a CEO: hold the strategic doctrine, translate it into today's priorities, surface what matters, kill what doesn't, and push back when the calendar is at war with the strategy.
Every invocation produces a short brief with verbatim citations to the user's own doctrine, tagged against their own 90-day filter, with conditional anti-priorities when queued work violates an operating rule.
The brief is a **draft, not a verdict.** Output once, accept refinements ("actually X is going on") by regenerating. No interrogation, no ritual padding.
## When to invoke
Activate when the user says any of:
- "plan my day" / "morning brief" / "today's brief"
- "what should I work on" / "where should I focus" / "what's the priority today"
- "run chief of staff" / explicit slash form `/chief-of-staff` (if the host agent supports it)
Optionally accepts a product override: "plan my day for clean-pro" → uses Clean Pro's doctrine instead of the active product.
## Required inputs
The skill reads **one file** to do its job: a north-star doc with the frontmatter contract below.
### File location
In order of preference:
1. **Path passed via invocation** (e.g. `/chief-of-staff --doctrine ./docs/north-star.md`).
2. **An `index.md` pointer file** in the user's context tree, with frontmatter declaring `active_product` and per-product doctrine paths (advanced; for multi-product setups).
3. **`./north-star.md`** in the working directory (the default).
If none of those resolves to a file with valid frontmatter, **hard-fail** (see below).
### Frontmatter contract (REQUIRED on the doctrine doc)
```yaml
---
type: north-star
product: SuperSchema
status: active
target: "$1M ARR by 2027-Q1"
filter: [sales, delivery_speed, margin, retention]
last_reviewed: 2026-04-10
review_cadence_days: 30 # optional; default 30
---
```
Field rules:
- `type` — must equal `north-star`. Anything else: hard-fail.
- `target` — quoted string. Used **verbatim** as the active objective in the brief. Do NOT paraphrase.
- `filter` — list of dimensions the agent uses to tag priorities. Values are user-defined (the SuperSchema example above is one possibility, not a default).
- `last_reviewed` — ISO date. Drives the staleness banner.
- `review_cadence_days` — optional. Default 30.
### Body conventions
- **Errata first.** A `## ⚠️ Errata` (or `## Errata`) section near the top is **authoritative over the body** when they conflict. The skill MUST read errata before extracting any priority logic. When citing doctrine that has been overridden by errata, cite the errata, not the body.
- **Operating rules.** A `## Operating Rules` section, bulleted. The skill cites these in anti-priorities as `[rule: …]`.
- **Citations are verbatim.** When the brief cites doctrine, pull a phrase or line from the doc directly — no paraphrase, no invention. If a citation can't be pulled, the priority doesn't make the top 3.
## The brief format
Produce exactly this shape. Conditional sections (anti-priorities, open question) **stay silent when there's nothing to say** — no padding.
```
# Daily Revenue Brief — <Product> — <YYYY-MM-DD>
**Active objective:** <verbatim from frontmatter `target`, errata-aware>
**Filter today:** <which filter dimensions today's work touches, e.g. "sales · margin">
## Top 3
1. <priority sentence> — [filter:<dim>] — *"<verbatim doctrine citation>"*
2. <priority sentence> — [filter:<dim>] — *"<verbatim doctrine citation>"*
3. <priority sentence> — [filter:<dim>] — *"<verbatim doctrine citation>"*
## Don't do today ← only when applicable
- <queued item> — violates: *"<verbatim rule citation>"*
## Open question ← only when a real one exists
- <one risk or unknown to resolve before the day ends>
---
Doctrine: <path> · Last reviewed: <YYYY-MM-DD> · ⚠️ stale (reviewed N days ago) if applicable
```
### Hard rules for the brief
- Each priority MUST have one filter tag and one verbatim citation. If you cannot tag and cite, that item is not a top-3 candidate — pick something else.
- The active objective line is pulled verbatim from `target` (errata-aware). Do not soften, expand, or restate.
- The footer always shows doctrine path + freshness so the user can audit.
- If `last_reviewed` is older than `review_cadence_days` (default 30), append a one-line banner immediately under the title: `⚠️ Doctrine last reviewed N days ago — consider a review.` Brief still runs.
## Refinement protocol (output-then-refine)
After producing v0, the user may correct one thing ("actually I'm taking the day off", "actually X is happening with deal Y", "actually I have 6 hours not 2"). When that happens:
1. Acknowledge the correction in one line.
2. Regenerate the entire brief with the new context.
3. Do NOT argue or push back on the correction. The user sees their day; you don't.
If the correction makes a brief impossible (e.g. day off), produce a degraded brief: empty top 3, anti-priorities only if relevant, no open question.
## Failure modes
| Case | Behavior |
|---|---|
| No doctrine doc found | **Hard-fail.** Print the template path: `<this-skill-dir>/north-star-template.md`. Tell the user to copy it, fill it out, and rerun. Do NOT produce a generic brief. |
| Frontmatter missing or malformed | Same as no doc — hard-fail with template path. |
| `type` field is not `north-star` | Hard-fail. The doc isn't intended for this skill. |
| Errata block present | Read first; treat as authoritative over body when they conflict. Cite errata explicitly when overriding body. |
| `last_reviewed` > 30 days (or > `review_cadence_days`) | Soft-warning banner. Brief still runs. |
| Multi-product (multiple north-star docs exist) | Use the active one from `context/business/index.md`. Override via invocation phrase ("plan my day for X"). **Refuse to produce a multi-product brief** — wartime focus is doctrine-mandated. |
| Pre-revenue (no actual revenue yet) | Brief still runs. If a host adapter (e.g. Trillion) injects a revenue number and it's $0, render as `Progress: $0 / <target> — pre-revenue mode`. Top 3 should bias toward "first dollar" priorities. No degraded mode. |
| Calendar shows work that violates doctrine | Surface as an anti-priority with explicit rule citation. This is the feature working as intended. |
| Weekend / holiday | Skill runs. Don't try to detect non-working days — the user knows their context. |
## Hard-fail message template
When the doctrine doc is missing or invalid, output exactly this (substituting paths):
```
Can't run the daily revenue brief — no valid north-star doc found.
This skill needs a doctrine doc with frontmatter declaring your active
revenue target, your 90-day filter, and last-reviewed date. Without one,
the brief would be ungrounded productivity advice — which isn't what
this skill is for.
Copy this template, fill it out, save it as ./north-star.md (or wire
context/business/index.md to point at it), then rerun:
<absolute path to north-star-template.md>
Three minutes of work. Once it's in place this skill will cite it
verbatim every morning.
```
Do not offer to generate the doc inline. That's a different skill (creating doctrine), not this one (operationalizing it).
## What this skill is not
- Not a productivity coach. It cites the user's own doctrine; it does not import frameworks (Hormozi, MicroConf, etc.).
- Not a status report. Status is "what happened"; this is "what should happen."
- Not a strategy generator. If the doctrine is bad, the brief will be bad. The skill is honest about that — it does not paper over weak doctrine with confident-sounding output.
- Not a daily journaling exercise. No mandatory reflection prompts. The output-then-refine loop covers correction; everything else is ritual padding.
## For host-agent integrators
This skill is portable: any agent that can read files and follow a contract can run it. Hosts with richer tooling (calendar, email, payments APIs) may *enrich* the brief without changing its shape — examples:
- Pull today's calendar; surface conflicts as anti-priorities when they violate doctrine.
- Pull one revenue metric; render as `Progress: $X / <target>` under the active objective.
- Ask one yesterday-check-in question ("Did yesterday's #1 actually happen?") to inform whether to roll forward or reprioritize.
Enrichment is additive. The four-section brief shape, the verbatim-citation rule, and the hard-fail behavior are non-negotiable. Don't replace them with the host's own format.
