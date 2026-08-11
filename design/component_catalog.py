"""Component palette — curated catalog for the design agent.

Each entry maps a component to its library, install command, and what it's
good for.  The catalog is consumed in two ways:
  1. `render_for_prompt()` — compact block the agent reads before dispatching.
  2. `render_full_catalog_markdown()` — verbose reference dropped into
     `.prism/preview/prism/component_catalog.md` for Claude Code (or the
     local LLM) to read at dispatch time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    library: str          # "shadcn" | "magicui" | "aceternity" | "reactbits"
    use_for: str          # one-line description
    install: str = ""     # CLI install command (empty = copy-paste only)
    docs: str = ""        # reference URL


# ── shadcn/ui ────────────────────────────────────────────────────────────────
SHADCN: list[CatalogEntry] = [
    CatalogEntry("button",      "shadcn", "Primary actions, CTAs, ghost variants",           "npx shadcn@latest add button"),
    CatalogEntry("card",        "shadcn", "Content containers, data display",                "npx shadcn@latest add card"),
    CatalogEntry("dialog",      "shadcn", "Modal overlays, confirmations",                   "npx shadcn@latest add dialog"),
    CatalogEntry("sheet",       "shadcn", "Side panels, detail views",                       "npx shadcn@latest add sheet"),
    CatalogEntry("tabs",        "shadcn", "Section switching, grouped content",              "npx shadcn@latest add tabs"),
    CatalogEntry("input",       "shadcn", "Text fields, search bars",                        "npx shadcn@latest add input"),
    CatalogEntry("form",        "shadcn", "Validated form layouts",                          "npx shadcn@latest add form"),
    CatalogEntry("select",      "shadcn", "Dropdown selectors",                              "npx shadcn@latest add select"),
    CatalogEntry("badge",       "shadcn", "Status indicators, tags",                         "npx shadcn@latest add badge"),
    CatalogEntry("table",       "shadcn", "Structured data grids",                           "npx shadcn@latest add table"),
    CatalogEntry("dropdown-menu", "shadcn", "Context menus, action menus",                   "npx shadcn@latest add dropdown-menu"),
    CatalogEntry("tooltip",     "shadcn", "Hover hints, info popups",                        "npx shadcn@latest add tooltip"),
    CatalogEntry("avatar",      "shadcn", "User initials, profile images",                   "npx shadcn@latest add avatar"),
    CatalogEntry("switch",      "shadcn", "Boolean toggles",                                 "npx shadcn@latest add switch"),
    CatalogEntry("slider",      "shadcn", "Range inputs, volume controls",                   "npx shadcn@latest add slider"),
    CatalogEntry("progress",    "shadcn", "Loading bars, completion indicators",             "npx shadcn@latest add progress"),
    CatalogEntry("separator",   "shadcn", "Visual dividers",                                 "npx shadcn@latest add separator"),
    CatalogEntry("skeleton",    "shadcn", "Loading placeholders",                            "npx shadcn@latest add skeleton"),
    CatalogEntry("textarea",    "shadcn", "Multi-line text input",                           "npx shadcn@latest add textarea"),
    CatalogEntry("accordion",   "shadcn", "Collapsible sections, FAQ",                       "npx shadcn@latest add accordion"),
]

# ── MagicUI ──────────────────────────────────────────────────────────────────
MAGICUI: list[CatalogEntry] = [
    CatalogEntry("particles",        "magicui", "Animated particle backgrounds",             "npx magicui-cli add particles"),
    CatalogEntry("marquee",          "magicui", "Auto-scrolling text/image strips",          "npx magicui-cli add marquee"),
    CatalogEntry("sparkles",         "magicui", "Hover/click sparkle effects",               "npx magicui-cli add sparkles"),
    CatalogEntry("animated-list",    "magicui", "Items that slide in sequentially",          "npx magicui-cli add animated-list"),
    CatalogEntry("bento-grid",       "magicui", "Asymmetric dashboard layouts",              "npx magicui-cli add bento-grid"),
    CatalogEntry("blur-fade",        "magicui", "Elements that blur-fade in on scroll",      "npx magicui-cli add blur-fade"),
    CatalogEntry("text-reveal",      "magicui", "Character-by-character text animation",     "npx magicui-cli add text-reveal"),
    CatalogEntry("border-beam",      "magicui", "Animated border light on cards/CTAs",       "npx magicui-cli add border-beam"),
    CatalogEntry("shimmer-button",   "magicui", "CTAs with a shimmer sweep",                 "npx magicui-cli add shimmer-button"),
    CatalogEntry(" globe",           "magicui", "3D rotating globe visualization",           "npx magicui-cli add globe"),
]

# ── Aceternity (copy-paste only — no CLI) ────────────────────────────────────
ACETERNITY: list[CatalogEntry] = [
    CatalogEntry("spotlight",       "aceternity", "Follow-cursor spotlight effect",         docs="https://ui.aceternity.com/components/spotlight"),
    CatalogEntry("background-beams","aceternity", "Animated beam grid background",          docs="https://ui.aceternity.com/components/background-beams"),
    CatalogEntry("tracing-beam",    "aceternity", "Scroll-following vertical beam",          docs="https://ui.aceternity.com/components/tracing-beam"),
    CatalogEntry("3d-card",         "aceternity", "3D tilt-on-hover card effect",            docs="https://ui.aceternity.com/components/3d-card-effect"),
]

# ── Reactbits (copy-paste only) ──────────────────────────────────────────────
REACTBITS: list[CatalogEntry] = [
    CatalogEntry("text-type",    "reactbits", "Typewriter text animation",                    docs="https://www.reactbits.dev/components/text-type"),
    CatalogEntry("scroll-reveal","reactbits", "Scroll-triggered reveal animations",           docs="https://www.reactbits.dev/components/scroll-reveal"),
]

ALL: list[CatalogEntry] = SHADCN + MAGICUI + ACETERNITY + REACTBITS


# ── Google Fonts catalog ─────────────────────────────────────────────────────
# Curated families across three roles: display, body, mono.
# ORDER MATTERS — most distinctive choices first.
GOOGLE_FONTS: dict[str, list[str]] = {
    "display": [
        "Space Grotesk",      # geometric, techy
        "Outfit",             # clean geometric
        "Sora",               # soft geometric
        "Plus Jakarta Sans",  # modern sans
        "Inter",              # neutral fallback
    ],
    "body": [
        "Inter",
        "DM Sans",
        "Nunito",
        "Source Sans 3",
    ],
    "mono": [
        "JetBrains Mono",
        "Fira Code",
        "IBM Plex Mono",
        "Source Code Pro",
    ],
}

# Fonts that signal "generic AI SaaS" — blocked at validation time.
FORBIDDEN_FAMILIES: frozenset[str] = frozenset({
    "Poppins", "Montserrat", "Raleway", "Open Sans", "Lato", "Roboto",
})


# ── Render helpers ───────────────────────────────────────────────────────────

def render_for_prompt(entries: list[CatalogEntry] | None = None) -> str:
    """Compact rendering for the system prompt — one line per component."""
    entries = entries or ALL
    lines = ["Available components:"]
    for e in entries:
        install = f" ({e.install})" if e.install else " (copy-paste)"
        lines.append(f"  - {e.name} [{e.library}]{install}: {e.use_for}")
    return "\n".join(lines)


def render_full_catalog_markdown() -> str:
    """Verbose reference for the preview app's component_catalog.md."""
    lines = ["# Component Catalog\n"]
    lines.append("Curated palette for the design agent. Each component is\n")
    lines.append("installed via CLI (shadcn/MagicUI) or copied from source.\n")
    for lib_name, entries in [("shadcn/ui", SHADCN), ("MagicUI", MAGICUI),
                               ("Aceternity (copy-paste)", ACETERNITY),
                               ("Reactbits (copy-paste)", REACTBITS)]:
        lines.append(f"\n## {lib_name}\n")
        lines.append("| Component | Install | Use for |")
        lines.append("|-----------|---------|---------|")
        for e in entries:
            inst = f"`{e.install}`" if e.install else "copy from docs"
            doc_link = f" ([docs]({e.docs}))" if e.docs else ""
            lines.append(f"| {e.name} | {inst} | {e.use_for}{doc_link} |")
    lines.append("\n## Google Fonts\n")
    lines.append("| Role | Families (first = preferred) |")
    lines.append("|------|------------------------------|")
    for role, families in GOOGLE_FONTS.items():
        lines.append(f"| {role} | {', '.join(families)} |")
    lines.append(f"\n**Forbidden:** {', '.join(sorted(FORBIDDEN_FAMILIES))}\n")
    return "\n".join(lines)
