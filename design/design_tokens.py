"""Design token parser — reads the YAML block from design.md, validates it,
and renders Tailwind configuration files.

The design.md file contains a ```yaml tokens` block that encodes fonts,
colors, radius, and shadcn theme settings.  This module parses that block,
validates it against curated catalogs, and generates the two files the
Next.js scaffold needs: `tailwind.config.ts` and `app/globals.css`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from design.component_catalog import FORBIDDEN_FAMILIES, GOOGLE_FONTS

# ── data model ───────────────────────────────────────────────────────────────

@dataclass
class DesignTokens:
    fonts: dict[str, str] = field(default_factory=lambda: {"display": "Inter", "body": "Inter", "mono": "JetBrains Mono"})
    colors: dict[str, str] = field(default_factory=lambda: {"primary": "#2dd4a8", "background": "#0a0f1a", "surface": "#111827", "text": "#e8fffb", "muted": "#6b7280"})
    radius: str = "0.75rem"
    shadcn_base: str = "zinc"


# ── YAML parsing (minimal — no pyyaml dependency) ────────────────────────────

_YAML_BLOCK_RE = re.compile(r"```yaml\s*tokens\s*\n(.*?)```", re.DOTALL)
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _parse_yaml_minimal(text: str) -> dict:
    """Minimal YAML parser for the tokens block — handles flat keys and
    nested dicts (one level deep).  No arrays, no aliases, no flow style.
    Good enough for the tokens block without adding a pyyaml dependency."""
    result: dict = {}
    current_section: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if indent == 0 and not val:
            # Section header (e.g. "fonts:")
            current_section = key
            result.setdefault(key, {})
        elif indent == 0 and val:
            # Top-level key: value
            result[key] = val
        elif current_section and indent > 0:
            result[current_section][key] = val
    return result


def parse_tokens(design_md: str) -> Optional[DesignTokens]:
    """Extract and parse the ```yaml tokens block from design.md."""
    m = _YAML_BLOCK_RE.search(design_md)
    if not m:
        return None
    raw = _parse_yaml_minimal(m.group(1))
    fonts = raw.get("fonts", {})
    colors = raw.get("colors", {})
    return DesignTokens(
        fonts={k: str(v) for k, v in fonts.items()} if fonts else DesignTokens().fonts,
        colors={k: str(v) for k, v in colors.items()} if colors else DesignTokens().colors,
        radius=str(raw.get("radius", "0.75rem")),
        shadcn_base=str(raw.get("shadcn_base", "zinc")),
    )


# ── validation ───────────────────────────────────────────────────────────────

def validate_tokens(tokens: DesignTokens) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors: list[str] = []
    all_families = [f for families in GOOGLE_FONTS.values() for f in families]
    for role, name in tokens.fonts.items():
        if name in FORBIDDEN_FAMILIES:
            errors.append(f"font '{name}' is in the FORBIDDEN_FAMILIES set (signals generic AI SaaS)")
        # Don't require all fonts to be in the catalog — custom fonts are OK
    for name, hex_val in tokens.colors.items():
        if not _HEX_RE.match(hex_val):
            errors.append(f"color '{name}' value '{hex_val}' is not a valid hex string (#RRGGBB)")
    valid_bases = {"zinc", "slate", "stone", "gray", "neutral", "red", "orange", "amber", "yellow", "lime", "green", "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose"}
    if tokens.shadcn_base not in valid_bases:
        errors.append(f"shadcn_base '{tokens.shadcn_base}' is not a valid shadcn base color")
    return errors


# ── renderers ────────────────────────────────────────────────────────────────

def _hex_to_hsl(hex_str: str) -> str:
    """Convert #RRGGBB to 'H S% L%' for Tailwind CSS variables."""
    h = hex_str.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    if mx == mn:
        h_deg = s = 0.0
    else:
        d = mx - mn
        s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
        if mx == r:
            h_deg = (g - b) / d + (6 if g < b else 0)
        elif mx == g:
            h_deg = (b - r) / d + 2
        else:
            h_deg = (r - g) / d + 4
        h_deg /= 6
    return f"{round(h_deg * 360)} {round(s * 100)}% {round(l * 100)}%"


def render_tailwind_config(tokens: DesignTokens) -> str:
    """Render a tailwind.config.ts from the parsed tokens."""
    font_family = ", ".join(f"'{v}'" for v in tokens.fonts.values())
    # Use .replace() instead of f-string to avoid brace-escaping issues
    # with Tailwind's {tsx,ts,jsx,js} glob syntax.
    return (
        'import type { Config } from "tailwindcss";\n\n'
        'const config: Config = {\n'
        '  darkMode: "class",\n'
        '  content: ["./app/**/*.{tsx,ts,jsx,js}", "./components/**/*.{tsx,ts,jsx,js}"],\n'
        '  theme: {\n'
        '    extend: {\n'
        '      fontFamily: {\n'
        '        sans: [__FONT_FAMILY__, "system-ui", "sans-serif"],\n'
        '      },\n'
        '      colors: {\n'
        '        border: "hsl(var(--border))",\n'
        '        input: "hsl(var(--input))",\n'
        '        ring: "hsl(var(--ring))",\n'
        '        background: "hsl(var(--background))",\n'
        '        foreground: "hsl(var(--foreground))",\n'
        '        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },\n'
        '        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },\n'
        '        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },\n'
        '        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },\n'
        '        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },\n'
        '        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },\n'
        '      },\n'
        '      borderRadius: {\n'
        '        lg: "var(--radius)",\n'
        '        md: "calc(var(--radius) - 2px)",\n'
        '        sm: "calc(var(--radius) - 4px)",\n'
        '      },\n'
        '    },\n'
        '  },\n'
        '  plugins: [require("tailwindcss-animate")],\n'
        '};\n\n'
        'export default config;\n'
    ).replace("__FONT_FAMILY__", font_family)


def render_globals_css(tokens: DesignTokens) -> str:
    """Render app/globals.css from the parsed tokens."""
    primary_hsl = _hex_to_hsl(tokens.colors.get("primary", "#2dd4a8"))
    bg_hsl = _hex_to_hsl(tokens.colors.get("background", "#0a0f1a"))
    surface_hsl = _hex_to_hsl(tokens.colors.get("surface", "#111827"))
    text_hsl = _hex_to_hsl(tokens.colors.get("text", "#e8fffb"))
    muted_hsl = _hex_to_hsl(tokens.colors.get("muted", "#6b7280"))
    return f"""\
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {{
  :root {{
    --background: {bg_hsl};
    --foreground: {text_hsl};
    --card: {surface_hsl};
    --card-foreground: {text_hsl};
    --primary: {primary_hsl};
    --primary-foreground: 0 0% 100%;
    --secondary: {surface_hsl};
    --secondary-foreground: {text_hsl};
    --muted: {muted_hsl};
    --muted-foreground: 0 0% 60%;
    --accent: {surface_hsl};
    --accent-foreground: {text_hsl};
    --destructive: 0 84% 60%;
    --destructive-foreground: 0 0% 100%;
    --border: 0 0% 15%;
    --input: 0 0% 15%;
    --ring: {primary_hsl};
    --radius: {tokens.radius};
  }}
}}

@layer base {{
  * {{
    @apply border-border;
  }}
  body {{
    @apply bg-background text-foreground;
    font-feature-settings: "rlig" 1, "calt" 1;
  }}
}}
"""
