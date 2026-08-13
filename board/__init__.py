"""Board of Advisors — a standing council of documented operators for Emma.

Tier map:
- models   — seat / opinion / meeting structures
- dossier  — markdown dossier parser (Tier 1)
- citations— the citation gate (Tier 1)
- router   — seat selection, name normalization, decline gate (Tier 3)
- brief    — the live business brief the chair reads (Tier 4/5)
- meeting  — the fan-out, one isolated model call per seat (Tier 4)
- chair    — synthesis, unanimity guard, spoken-summary guard (Tier 5)
- store    — meeting storage, citation snapshots, retirement (Tier 6)
- scheduler— the standing monthly review (Tier 7)
"""
