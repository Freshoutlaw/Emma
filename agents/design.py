"""Design agent — head-of-design for Emma.

Reads and writes three-tier design documents (design.md, brief.md, feature specs),
generates mockups by having Emma's local LLM write TSX into a Next.js scaffold,
and runs `npm run build` to produce static HTML.

Adapted from the head-of-design playbook for Emma's local stack:
  - No Claude Code CLI — the local LLM (Ollama/Groq) writes the TSX directly.
  - No Gemini API — no AI image generation (Tier 5 deferred).
  - Zero paid dependencies — everything runs locally.

The agent is a skeleton until the first dispatch.  On first use it bootstraps
the design docs (design.md, brief.md) from a repo scan.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from agents.base import AgentResult, BaseAgent
from design.component_catalog import render_for_prompt
from design.design_tokens import DesignTokens, parse_tokens, validate_tokens
from design.docs import (
    bootstrap,
    design_doc_path,
    feature_doc_path,
    read_design_doc,
    read_feature_doc,
    write_feature_doc,
)
from design.scaffold import ScaffoldResult, prepare_scaffold

if TYPE_CHECKING:
    from agents.router import Pipeline


# ── constants ────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── agent ────────────────────────────────────────────────────────────────────

class DesignAgent(BaseAgent):
    name = "design"
    description = "Reads/writes design docs and generates mockup screens via the local LLM + Next.js scaffold."

    # Least-privilege: the design agent needs file read/write for its own
    # docs and the scaffold, but NOT the full ControlAgent catalog.
    tool_allowlist: frozenset[str] = frozenset({
        "read_file", "write_file", "list_dir",
    })

    def __init__(self, pipeline: "Pipeline") -> None:
        super().__init__(pipeline)

    # ---------------------------------------------------------------- bootstrap
    async def bootstrap(self) -> AgentResult:
        """Generate starter design.md and brief.md if they don't exist."""
        created = bootstrap()
        if not created:
            return AgentResult(
                ok=True,
                output="Design docs already exist at context/design/. No action needed.",
                intent="design",
            )
        lines = ["Bootstrapped design docs:"]
        for name, path in created.items():
            lines.append(f"  ✔ {name} → {path}")
        return AgentResult(ok=True, output="\n".join(lines), intent="design")

    # ---------------------------------------------------------------- tokens
    async def show_tokens(self) -> AgentResult:
        """Parse and display the current design tokens."""
        doc = read_design_doc()
        if not doc:
            return AgentResult(
                ok=False,
                output="No design.md found. Run 'design bootstrap' first.",
                intent="design",
                error="no design doc",
            )
        tokens = parse_tokens(doc)
        if not tokens:
            return AgentResult(
                ok=False,
                output="No ```yaml tokens block found in design.md.",
                intent="design",
                error="no tokens block",
            )
        errors = validate_tokens(tokens)
        status = "✔ valid" if not errors else f"✘ {len(errors)} error(s)"
        lines = [
            f"Design tokens — {status}",
            f"  fonts: display={tokens.fonts.get('display')}, body={tokens.fonts.get('body')}, mono={tokens.fonts.get('mono')}",
            f"  colors: {json.dumps(tokens.colors)}",
            f"  radius: {tokens.radius}",
            f"  shadcn_base: {tokens.shadcn_base}",
        ]
        if errors:
            lines.append("  errors:")
            for e in errors:
                lines.append(f"    - {e}")
        return AgentResult(ok=True, output="\n".join(lines), intent="design")

    # ---------------------------------------------------------------- scaffold
    async def scaffold(self, slug: str = "emma") -> AgentResult:
        """Create or verify the Next.js preview scaffold."""
        doc = read_design_doc()
        if not doc:
            return AgentResult(
                ok=False,
                output="No design.md found. Run 'design bootstrap' first.",
                intent="design",
                error="no design doc",
            )
        tokens = parse_tokens(doc)
        if not tokens:
            tokens = DesignTokens()  # use defaults

        result: ScaffoldResult = prepare_scaffold(PROJECT_ROOT, tokens, project_slug=slug)
        if result.created:
            lines = [f"✔ Scaffold created at {result.preview_dir}"]
            lines.append(f"  Files: {', '.join(result.files)}")
            lines.append(f"  Run: cd {result.preview_dir} && npm install && npm run build")
            return AgentResult(ok=True, output="\n".join(lines), intent="design")
        else:
            return AgentResult(
                ok=True,
                output=f"Scaffold already exists at {result.preview_dir} (idempotent skip).",
                intent="design",
            )

    # ---------------------------------------------------------------- build
    async def build(self, slug: str = "emma") -> AgentResult:
        """Install deps and build the preview app."""
        preview_dir = PROJECT_ROOT / ".prism" / "preview"
        if not (preview_dir / "package.json").exists():
            return AgentResult(
                ok=False,
                output=f"No scaffold at {preview_dir}. Run 'design scaffold' first.",
                intent="design",
                error="no scaffold",
            )

        try:
            # npm install
            install = subprocess.run(
                [sys.executable, "-m", "npm", "install"],
                cwd=str(preview_dir),
                capture_output=True, text=True, timeout=120,
            )
            if install.returncode != 0:
                return AgentResult(
                    ok=False,
                    output=f"npm install failed:\n{install.stderr[:1000]}",
                    intent="design",
                    error="npm install failed",
                )

            # npm run build
            build = subprocess.run(
                [sys.executable, "-m", "npm", "run", "build"],
                cwd=str(preview_dir),
                capture_output=True, text=True, timeout=120,
            )
            if build.returncode != 0:
                return AgentResult(
                    ok=False,
                    output=f"npm run build failed:\n{build.stderr[:1000]}",
                    intent="design",
                    error="build failed",
                )

            out_dir = preview_dir / "out"
            return AgentResult(
                ok=True,
                output=f"✔ Build complete. Static export at {out_dir}",
                intent="design",
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                ok=False,
                output="Build timed out (>120s).",
                intent="design",
                error="timeout",
            )

    # ---------------------------------------------------------------- catalog
    async def catalog(self) -> AgentResult:
        """Show the available component palette."""
        return AgentResult(ok=True, output=render_for_prompt(), intent="design")

    # ---------------------------------------------------------------- plan mockup
    async def plan_mockup(self, feature: str, description: str) -> AgentResult:
        """Plan a mockup screen: write a feature spec and scaffold the page.

        The actual TSX generation is deferred to the local LLM (the reasoning
        agent or a manual step) since we don't have Claude Code CLI.
        """
        doc = read_design_doc()
        if not doc:
            return AgentResult(
                ok=False,
                output="No design.md found. Run 'design bootstrap' first.",
                intent="design",
                error="no design doc",
            )

        tokens = parse_tokens(doc)
        if not tokens:
            tokens = DesignTokens()

        brief_doc = read_design_doc()  # brief is separate but we read design for tokens

        # Write the feature spec
        spec = f"""\
---
type: feature-spec
feature: {feature}
product: Emma
status: draft
---
# {feature} — Feature Spec

## Overview

{description}

## Components

{render_for_prompt()}

## Visual Direction

<!-- To be filled by the local LLM during generation. -->
"""
        path = write_feature_doc(feature, spec)

        # Create the page directory in the scaffold
        preview_dir = PROJECT_ROOT / ".prism" / "preview"
        page_dir = preview_dir / "app" / feature
        page_dir.mkdir(parents=True, exist_ok=True)

        # Write a placeholder page
        placeholder = f"""\
export default function {feature.replace('-', '').title()}Page() {{
  return (
    <main className="min-h-screen bg-background text-foreground flex items-center justify-center">
      <div className="text-center space-y-4">
        <h1 className="text-4xl font-bold">{feature.replace('-', ' ').title()}</h1>
        <p className="text-muted-foreground">Mockup placeholder — generate TSX via the local LLM.</p>
      </div>
    </main>
  );
}}
"""
        (page_dir / "page.tsx").write_text(placeholder, encoding="utf-8")

        return AgentResult(
            ok=True,
            output=(
                f"✔ Feature spec written to {path}\n"
                f"  Page scaffold at {page_dir / 'page.tsx'}\n"
                f"  Next step: use the local LLM to generate the actual TSX,\n"
                f"  then run 'design build' to produce static HTML."
            ),
            intent="design",
        )

    # ---------------------------------------------------------------- run
    async def run(self, request: str) -> AgentResult:
        """Natural-language entry point."""
        low = request.strip().lower()

        if "bootstrap" in low:
            return await self.bootstrap()
        if "token" in low:
            return await self.show_tokens()
        if "scaffold" in low:
            return await self.scaffold()
        if "build" in low:
            return await self.build()
        if "catalog" in low or "component" in low:
            return await self.catalog()
        if "mockup" in low or "design" in low or "screen" in low:
            # Extract feature name if provided
            feature = "untitled"
            description = request
            for prefix in ("design ", "mockup ", "screen "):
                if low.startswith(prefix):
                    rest = request[len(prefix):].strip()
                    words = rest.split(None, 1)
                    if words:
                        feature = words[0].lower().replace(" ", "-")
                        description = words[1] if len(words) > 1 else rest
                    break
            return await self.plan_mockup(feature, description)

        return AgentResult(
            ok=True,
            output=(
                "Design agent — commands:\n"
                "  bootstrap  — create starter design.md and brief.md\n"
                "  tokens     — show current design tokens\n"
                "  scaffold   — create Next.js preview app\n"
                "  build      — install deps and build preview\n"
                "  catalog    — show available components\n"
                "  mockup <feature> <description> — plan a mockup screen"
            ),
            intent="design",
        )
