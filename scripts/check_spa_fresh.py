#!/usr/bin/env python3
"""CI guard: fail if the served SPA bundle is stale versus the web/ source.

The backend serves a prebuilt, committed bundle at
``src/attest/api/static/index.html`` (see ``attest.api.frontend``). It once went
stale silently — web/ changes were merged but never rebuilt into the bundle, so
the running app kept serving the old UI. ``build_spa.py`` now stamps the bundle
with a fingerprint of the web/ source it was built from; this recomputes that
fingerprint and fails if the committed bundle doesn't match, so the staleness is
caught in CI instead of in production.

Needs only Python + the repo — no Node, no build.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_spa import MARKER_RE, OUT, ROOT, source_fingerprint  # noqa: E402


def main() -> int:
    rel = OUT.relative_to(ROOT)
    if not OUT.is_file():
        print(f"error: {rel} is missing — run `python scripts/build_spa.py`", file=sys.stderr)
        return 1

    match = MARKER_RE.search(OUT.read_text(encoding="utf-8"))
    if match is None:
        print(
            f"error: {rel} has no source fingerprint — rebuild it with "
            "`python scripts/build_spa.py`",
            file=sys.stderr,
        )
        return 1

    expected = source_fingerprint()
    if match.group(1) != expected:
        print(
            f"error: {rel} is STALE versus web/ source.\n"
            "       The web/ workspace changed but the served bundle wasn't rebuilt.\n"
            "       Run `python scripts/build_spa.py` and commit the result.\n"
            f"       bundle: {match.group(1)[:12]}…  current source: {expected[:12]}…",
            file=sys.stderr,
        )
        return 1

    print(f"{rel} is up to date with web/ source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
