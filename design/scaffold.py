"""Per-project preview scaffold — Next.js + Tailwind + shadcn.

Creates a minimal Next.js application at `<project>/.prism/preview/` that
serves as the substrate for mockup composition.  The scaffold is idempotent:
if `package.json` already exists, it's skipped.

Key settings:
  - output: 'export' (static export, no Node runtime at view time)
  - trailingSlash: true (each route → out/<path>/index.html)
  - basePath / assetPrefix set to the serving URL prefix
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from design.design_tokens import DesignTokens, render_globals_css, render_tailwind_config


@dataclass
class ScaffoldResult:
    created: bool
    preview_dir: Path
    files: list[str]


def prepare_scaffold(
    project_root: Path,
    tokens: DesignTokens,
    project_slug: str = "emma",
    base_prefix: str = "/design/preview",
) -> ScaffoldResult:
    """Create (or skip) the Next.js scaffold at .prism/preview/."""
    preview_dir = project_root / ".prism" / "preview"

    # Idempotent: if package.json exists, skip scaffold creation
    if (preview_dir / "package.json").exists():
        return ScaffoldResult(created=False, preview_dir=preview_dir, files=[])

    preview_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    # ── package.json ─────────────────────────────────────────────────────
    pkg = {
        "name": f"{project_slug}-preview",
        "version": "0.1.0",
        "private": True,
        "scripts": {
            "dev": "next dev",
            "build": "next build",
            "start": "next start",
        },
        "dependencies": {
            "next": "^15.0.0",
            "react": "^19.0.0",
            "react-dom": "^19.0.0",
            "class-variance-authority": "^0.7.0",
            "clsx": "^2.1.0",
            "tailwind-merge": "^2.2.0",
            "lucide-react": "^0.400.0",
        },
        "devDependencies": {
            "@types/node": "^22.0.0",
            "@types/react": "^19.0.0",
            "autoprefixer": "^10.4.0",
            "postcss": "^8.4.0",
            "tailwindcss": "^3.4.0",
            "tailwindcss-animate": "^1.0.0",
            "typescript": "^5.5.0",
        },
    }
    _write(preview_dir / "package.json", json.dumps(pkg, indent=2) + "\n")
    files.append("package.json")

    # ── next.config.mjs ──────────────────────────────────────────────────
    config = f"""\
/** @type {{import('next').NextConfig}} */
const nextConfig = {{
  output: "export",
  trailingSlash: true,
  basePath: "{base_prefix}",
  assetPrefix: "{base_prefix}",
  images: {{
    unoptimized: true,
  }},
}};
export default nextConfig;
"""
    _write(preview_dir / "next.config.mjs", config)
    files.append("next.config.mjs")

    # ── tsconfig.json ────────────────────────────────────────────────────
    tsconfig = {
        "compilerOptions": {
            "target": "ES2017",
            "lib": ["dom", "dom.iterable", "esnext"],
            "allowJs": True,
            "skipLibCheck": True,
            "strict": True,
            "noEmit": True,
            "esModuleInterop": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "jsx": "preserve",
            "incremental": True,
            "plugins": [{"name": "next"}],
            "paths": {"@/*": ["./src/*"]},
        },
        "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
        "exclude": ["node_modules"],
    }
    _write(preview_dir / "tsconfig.json", json.dumps(tsconfig, indent=2) + "\n")
    files.append("tsconfig.json")

    # ── tailwind.config.ts ───────────────────────────────────────────────
    _write(preview_dir / "tailwind.config.ts", render_tailwind_config(tokens))
    files.append("tailwind.config.ts")

    # ── postcss.config.js ────────────────────────────────────────────────
    _write(preview_dir / "postcss.config.js", """\
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
""")
    files.append("postcss.config.js")

    # ── app/globals.css ──────────────────────────────────────────────────
    app_dir = preview_dir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    _write(app_dir / "globals.css", render_globals_css(tokens))
    files.append("app/globals.css")

    # ── app/layout.tsx ───────────────────────────────────────────────────
    families = ", ".join(f"'{v}'" for v in tokens.fonts.values())
    layout = f"""\
import type {{ Metadata }} from "next";
import "./globals.css";

export const metadata: Metadata = {{
  title: "{project_slug.capitalize()} Preview",
  description: "Generated mockup preview",
}};

export default function RootLayout({{ children }}: {{ children: React.ReactNode }}) {{
  return (
    <html lang="en" className="dark">
      <body>{{children}}</body>
    </html>
  );
}}
"""
    _write(app_dir / "layout.tsx", layout)
    files.append("app/layout.tsx")

    # ── app/page.tsx ─────────────────────────────────────────────────────
    page = f"""\
export default function Home() {{
  return (
    <main className="min-h-screen bg-background text-foreground flex items-center justify-center">
      <div className="text-center space-y-4">
        <h1 className="text-4xl font-bold">{project_slug.capitalize()} Preview</h1>
        <p className="text-muted-foreground">Generated mockups will appear here.</p>
      </div>
    </main>
  );
}}
"""
    _write(app_dir / "page.tsx", page)
    files.append("app/page.tsx")

    # ── lib/utils.ts ─────────────────────────────────────────────────────
    lib_dir = preview_dir / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    _write(lib_dir / "utils.ts", """\
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
""")
    files.append("lib/utils.ts")

    # ── prism directory for component catalog ─────────────────────────────
    prism_dir = preview_dir / "prism"
    prism_dir.mkdir(parents=True, exist_ok=True)

    return ScaffoldResult(created=True, preview_dir=preview_dir, files=files)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
