"""Design document helpers — resolve paths, read/write templates, bootstrap.

The design agent stores its working documents under `context/design/` in the
project root.  Each project gets a `design.md` (stable) and a `brief.md`
(evolving) plus a `features/` directory for per-feature specs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from design.design_tokens import DesignTokens, parse_tokens, validate_tokens


# ── path helpers ─────────────────────────────────────────────────────────────

def _project_root() -> Path:
    """Canonical project root — emma-ai repo root."""
    return Path(__file__).resolve().parent.parent


def design_dir() -> Path:
    return _project_root() / "context" / "design"


def features_dir() -> Path:
    return design_dir() / "features"


def design_doc_path() -> Path:
    return design_dir() / "design.md"


def brief_doc_path() -> Path:
    return design_dir() / "brief.md"


def feature_doc_path(slug: str) -> Path:
    safe = re.sub(r"[^a-z0-9\-]", "-", slug.lower().strip())
    return features_dir() / f"{safe}.md"


# ── read / write ─────────────────────────────────────────────────────────────

def read_design_doc() -> Optional[str]:
    p = design_doc_path()
    return p.read_text(encoding="utf-8") if p.exists() else None


def read_brief_doc() -> Optional[str]:
    p = brief_doc_path()
    return p.read_text(encoding="utf-8") if p.exists() else None


def read_feature_doc(slug: str) -> Optional[str]:
    p = feature_doc_path(slug)
    return p.read_text(encoding="utf-8") if p.exists() else None


def write_design_doc(content: str) -> Path:
    p = design_doc_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def write_brief_doc(content: str) -> Path:
    p = brief_doc_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def write_feature_doc(slug: str, content: str) -> Path:
    p = feature_doc_path(slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def list_project_files(exts: tuple[str, ...] = (".json", ".css", ".md", ".toml")) -> list[str]:
    """List files in the project root matching common config/doc extensions."""
    root = _project_root()
    results: list[str] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in exts and ".venv" not in str(p) and "node_modules" not in str(p):
            results.append(str(p.relative_to(root)))
    return sorted(results)[:50]


# ── bootstrap ────────────────────────────────────────────────────────────────

DESIGN_TEMPLATE = """\
---
type: design-system
product: {product}
tokens:
  fonts:
    display: "Inter"
    body: "Inter"
    mono: "JetBrains Mono"
  colors:
    primary: "#2dd4a8"
    background: "#0a0f1a"
    surface: "#111827"
    text: "#e8fffb"
    muted: "#6b7280"
  radius: "0.75rem"
  shadcn_base: "zinc"
last_reviewed: "{date}"
---
# {product} — Design System

## Tokens

The YAML block above defines the project's visual foundation.  The
`design_tokens.py` module renders `tailwind.config.ts` and
`app/globals.css` from these values.

## Principles

- **Dark-first.** Every surface is designed for dark mode first; light
  mode is a secondary concern.
- **One accent color.** Use `{primary}` for interactive elements and
  highlights; never more than one accent.
- **Restrained typography.** Display font for headlines only; body and
  mono for everything else.
- **Glass-morphism.** Surfaces use `backdrop-filter: blur()` with low-
  opacity backgrounds.

## Conventions

- All components use the shadcn/ui primitive set.
- Animations use Framer Motion or MagicUI.
- Spacing follows the Tailwind default scale (4px base).
"""


BRIEF_TEMPLATE = """\
---
type: design-brief
product: {product}
status: active
last_reviewed: "{date}"
---
# {product} — Design Brief

## Positioning

<!-- One sentence: what is this product, who is it for? -->

## Persona

<!-- Who is the primary user? One paragraph. -->

## Business Goals

<!-- What does winning look like in 90 days? -->

## Brand Language

<!-- 3-5 words that describe the visual tone (e.g. "precise, dark, confident") -->

## Standing Design Decisions

- Dark mode is the default.
- One accent color (`#2dd4a8`), no gradients on interactive elements.
- Typography: Inter for body, JetBrains Mono for data/code.

### Forbidden Moves

<!-- Things the design agent must never do. Be specific. -->
- No light mode as the primary experience.
- No more than 3 font sizes on a single screen.
- No animation faster than 300ms.
- No stock photography — illustrations or abstract shapes only.

## Notes

<!-- Anything else doctrinal. -->
"""


FEATURE_TEMPLATE = """\
---
type: feature-spec
feature: {feature}
product: {product}
status: draft
---
# {feature} — Feature Spec

## Overview

<!-- What is this feature? One paragraph. -->

## Screens

<!-- List the screens/views this feature needs. -->

## Components

<!-- Which shadcn/MagicUI components does this feature use? -->

## Visual Direction

<!-- Specific, not adjectives. Name colors, opacity, motion. -->
"""


def bootstrap(product_name: str = "Emma") -> dict[str, Path]:
    """Generate starter design.md and brief.md if they don't exist.

    Returns dict of created paths (empty if all already exist).
    """
    from datetime import date

    today = date.today().isoformat()
    created: dict[str, Path] = {}

    dp = design_doc_path()
    if not dp.exists():
        write_design_doc(DESIGN_TEMPLATE.format(
            product=product_name, date=today, primary="#2dd4a8",
        ))
        created["design.md"] = dp

    bp = brief_doc_path()
    if not bp.exists():
        write_brief_doc(BRIEF_TEMPLATE.format(
            product=product_name, date=today,
        ))
        created["brief.md"] = bp

    features_dir().mkdir(parents=True, exist_ok=True)
    return created
