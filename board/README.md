# Board of Advisors

A standing council of documented operators Emma can convene on a real
decision. Not one model wearing five hats — each seat gets one isolated
model call containing exactly one dossier, and every citation is gated
server-side against the doctrine that seat was actually shown.

## Convene

Say (or type) **"ask the board about <question>"** — e.g.
"ask the board about pricing our product at $20 vs $50 a month."

Other phrases:
- `board list` — the roster and each seat's standing
- `board status` — the last meeting
- `board retire <seat> <D#>` — retire a doctrine entry (ids are never reused)
- Naming an advisor also convenes: "what would Thompson say about building
  on OpenAI's platform?"

## Architecture

```
board/
  dossiers/        the seats — markdown: frontmatter, doctrine D1..Dn with
                   numbered ids + sources, characteristic objection,
                   blind spots, voice
  dossier.py       parser — rejects no-domains / no-doctrine / duplicate ids
  citations.py     the citation gate (pure function)
  router.py        seat selection (surname + domain), unicode-safe names,
                   decline gate
  brief.py         live brief from usage.db (+ business DB when configured)
  meeting.py       fan-out — one isolated call per seat, budgets, tolerance
  chair.py         synthesis + unanimity guard + spoken-summary guard
  store.py         meetings + citation snapshots + retirement/edit rules
  scheduler.py     monthly standing review (Tier 7)
  research/        the fact-check reports behind the dossiers
  research/verify.py
                   the independent adversarial fact-check (Tier 2, stage
                   two) — a SEPARATE model pass that assumes every dossier
                   is wrong and tries to refute each entry. Run it with:
                     python -m board.research.verify
                   It writes one report per seat (research/FACTCHECK_<id>.md)
                   so what was confirmed, corrected, and rejected is visible
                   per entry. The researcher is never the checker.
```

## Rules that are enforced in code, not prompts

- **Isolation is structural.** One call per seat, one dossier per system
  prompt. No seat knows the others exist.
- **Citation gate.** A seat may cite only the ids it was shown. Fabricated
  ids are stripped before you see anything.
- **A ceiling of zero means zero.** `EMMA_BOARD_COST_CEILING_USD` (default
  $0.50 per meeting) is checked before the fan-out starts and before every
  call.
- **Retire, never delete.** Retiring an id keeps it spoken for forever.
- **Verification state is server-owned.** An edit that changes an entry's
  substance drops it to `user` automatically.
- **One voice is never a consensus.** The unanimity guard is deterministic,
  and the spoken summary is checked against the computed verdict.

## Your live numbers

The chair reads the brief fresh at meeting time — never a number written
down in a file. Three real feeds:

1. `usage.db` — actual LLM spend (always available).
2. **What you told Emma.** Business numbers you state in chat land in
   episodic memory; at meeting time one bounded LLM call extracts them
   with provenance (episode id + date) into `data/business.db`. The brief
   labels them "reported to Emma — sourced, not verified financials", so
   the chair says *which episode* a figure rests on. Try:
   > "for the record, our MRR is $4,200"
3. **A real business database.** Set `EMMA_SUPABASE_QUERY_DSN` and the
   brief runs deterministic read-only probes at meeting time, showing the
   actual rows the chair saw.

When neither feed has data, the brief says exactly how to fix it — it
never invents a figure, and the chair is told not to either.
