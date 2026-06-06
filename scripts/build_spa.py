#!/usr/bin/env python3
"""Build the React workspace and inline it into the single file the backend serves.

`attest serve` returns ``src/attest/api/static/index.html`` at ``/`` (see
``attest.api.frontend``). Vite emits a small ``index.html`` plus separate JS/CSS
assets; this script inlines those assets into one self-contained ``index.html`` so
the served bundle has no asset dependencies (only the Google Fonts links remain).

Usage::

    python scripts/build_spa.py            # runs `npm run build` then inlines
    python scripts/build_spa.py --no-build  # inline an existing web/dist build

The figure-verification path talks to the backend on the same origin, so the
bundle is built with ``VITE_ATTEST_API`` unset (defaults to a relative base).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = WEB / "dist"
OUT = ROOT / "src" / "attest" / "api" / "static" / "index.html"

# The web/ inputs (besides web/src/**) that determine the built bundle. Build
# artifacts (dist, tsconfig.tsbuildinfo) and node_modules are deliberately out.
_FINGERPRINT_FILES = ("index.html", "package.json", "package-lock.json",
                      "tsconfig.json", "vite.config.ts")

# Trailing marker the build stamps into the bundle: a fingerprint of the web/
# source it was inlined from. scripts/check_spa_fresh.py recomputes the source
# fingerprint and fails CI if it no longer matches — so a web/ change that wasn't
# rebuilt into the served bundle can't silently ship the stale UI.
MARKER_RE = re.compile(r"<!-- attest-spa-src:([0-9a-f]{64}) -->")


def source_fingerprint() -> str:
    """A stable SHA-256 over the web/ source that determines the built bundle.

    Hashing the *source* (not Vite's output) makes the freshness check
    independent of Node/Vite build reproducibility: it answers "was the served
    bundle rebuilt from this exact source?", which is the thing that goes stale.
    """
    paths: list[Path] = [p for p in (WEB / "src").rglob("*") if p.is_file()]
    paths += [WEB / name for name in _FINGERPRINT_FILES if (WEB / name).is_file()]
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: x.relative_to(WEB).as_posix()):
        h.update(p.relative_to(WEB).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


SCRIPT_RE = re.compile(
    r'<script\b[^>]*\bsrc="(?P<src>/assets/[^"]+\.js)"[^>]*></script>'
)
STYLE_RE = re.compile(
    r'<link\b[^>]*\bhref="(?P<href>/assets/[^"]+\.css)"[^>]*>'
)


def _asset(path: str) -> str:
    """Read an emitted asset by its absolute (site-root) href."""
    return (DIST / path.lstrip("/")).read_text(encoding="utf-8")


def inline() -> str:
    html = (DIST / "index.html").read_text(encoding="utf-8")

    def sub_script(m: re.Match[str]) -> str:
        code = _asset(m.group("src"))
        return f'<script type="module" crossorigin>{code}</script>'

    def sub_style(m: re.Match[str]) -> str:
        css = _asset(m.group("href"))
        return f"<style>{css}</style>"

    html = SCRIPT_RE.sub(sub_script, html)
    html = STYLE_RE.sub(sub_style, html)
    return html


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--no-build", action="store_true", help="skip `npm run build`; inline web/dist as-is"
    )
    args = ap.parse_args()

    if not args.no_build:
        subprocess.run(["npm", "run", "build"], cwd=WEB, check=True)

    if not (DIST / "index.html").is_file():
        print("error: web/dist/index.html not found — run the web build first", file=sys.stderr)
        return 1

    html = inline()
    if "/assets/" in html:
        print("error: some /assets/ references were not inlined", file=sys.stderr)
        return 1

    # Stamp the source fingerprint so CI can detect a stale bundle (see MARKER_RE).
    html = f"{html}\n<!-- attest-spa-src:{source_fingerprint()} -->\n"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
