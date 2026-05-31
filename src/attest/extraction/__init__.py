"""Extraction — the replaceable probabilistic edge in front of the deterministic spine.

Two concerns live here, both *outside* the trust boundary:

* :mod:`attest.extraction.text` turns an uploaded file (txt/md/html/docx/pdf/rtf)
  into plain prose.
* :mod:`attest.extraction.claims` proposes the :class:`~attest.domain.verdicts.FigureClaim`
  candidates a model would otherwise propose, deterministically and model-free.

Nothing here can assert a tie-out; it only nominates candidates for the engine to
dispose of.
"""

from __future__ import annotations

from attest.extraction.claims import (
    DEFAULT_ALIASES,
    AliasConfig,
    ClaimExtractor,
    infer_period,
)
from attest.extraction.text import ExtractedText, extract_text

__all__ = [
    "AliasConfig",
    "DEFAULT_ALIASES",
    "ClaimExtractor",
    "infer_period",
    "ExtractedText",
    "extract_text",
]
