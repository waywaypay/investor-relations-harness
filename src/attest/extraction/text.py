"""Turn an uploaded file into plain prose.

The verification engine and the rules only ever read a document's *text*; how that
text arrived — a pasted draft, a ``.docx`` of prepared remarks, a press-release
PDF — is an extraction concern that lives here, not in the spine. Extraction is
deliberately best-effort and *honest*: every connector reports what it could and
could not recover (``ExtractedText.warnings``) so a half-readable PDF never
masquerades as a clean draft.

Stdlib only: text formats decode directly, ``.docx`` is unzipped XML, ``.pdf`` is
a best-effort pass over (optionally Flate-compressed) content streams. Anything we
cannot confidently recover is surfaced as a warning, never silently dropped.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
import zlib
from dataclasses import dataclass, field

_TEXT_EXTS = {
    "txt", "text", "md", "markdown", "mdown", "rst",
    "csv", "tsv", "log", "json", "yaml", "yml",
}
_HTML_EXTS = {"html", "htm", "xhtml"}

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
# Collapse any run of horizontal whitespace — crucially including the non-breaking
# space (U+00A0, from HTML &nbsp;) and other Unicode spaces — to a single ASCII
# space, while preserving newlines. Real IR HTML/Word peppers &nbsp; between a
# label's words and between a number and its scale word ("Operating&nbsp;cash&nbsp;
# flow", "$1.24&nbsp;billion"); left as U+00A0 those break alias matching (a figure
# is mis-attributed) and litter the displayed text. ``[^\S\n]`` is "whitespace that
# is not a newline", so paragraph structure survives.
_WS_RUN_RE = re.compile(r"[^\S\n]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n\s*\n+")


@dataclass(frozen=True)
class ExtractedText:
    """The recovered prose plus an honest account of what happened."""

    text: str
    kind: str  # the connector that produced it: 'text' | 'html' | 'docx' | 'pdf' | 'rtf'
    warnings: list[str] = field(default_factory=list)


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _tidy(text: str) -> str:
    """Normalise whitespace without destroying paragraph structure."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RUN_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


def _strip_html(raw: str) -> str:
    raw = _SCRIPT_STYLE_RE.sub(" ", raw)
    raw = re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>", "\n", raw, flags=re.IGNORECASE)
    raw = _TAG_RE.sub(" ", raw)
    return html.unescape(raw)


def _strip_rtf(raw: str) -> str:
    raw = re.sub(r"\\par[d]?\b", "\n", raw)
    raw = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw)        # hex-escaped bytes
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)       # control words
    raw = raw.replace("{", "").replace("}", "")
    return raw


def _extract_docx(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        return "", [f"Could not read .docx archive ({exc})."]
    # Paragraph and tab boundaries become real whitespace before we drop tags.
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    text = _TAG_RE.sub("", xml)
    text = html.unescape(text)
    if not text.strip():
        warnings.append("The .docx contained no extractable paragraph text.")
    return text, warnings


def _extract_pdf(data: bytes) -> tuple[str, list[str]]:
    """Best-effort PDF text recovery (stdlib only).

    Walks every ``stream``/``endstream`` block, inflates it when it is Flate
    compressed, and pulls the strings out of ``Tj`` / ``TJ`` text-showing
    operators. This handles a large share of text-based press-release PDFs; image
    or font-subset-only PDFs will recover little, which we report rather than hide.
    """
    warnings: list[str] = []
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL):
        raw = m.group(1)
        payload = raw
        try:
            payload = zlib.decompress(raw)
        except zlib.error:
            payload = raw  # uncompressed (or a filter we don't handle) — try as-is
        chunks.append(_pdf_strings(payload))
    text = "\n".join(c for c in chunks if c.strip())
    if not text.strip():
        warnings.append(
            "Could not extract text from this PDF (it may be scanned/image-only or "
            "use an unsupported compression). Paste the text instead for full analysis."
        )
    return text, warnings


def _pdf_strings(payload: bytes) -> str:
    try:
        s = payload.decode("latin-1")
    except UnicodeDecodeError:  # pragma: no cover - latin-1 maps every byte
        return ""
    out: list[str] = []
    # ( ... ) Tj   and   [ (..) -nn (..) ] TJ   text-showing operators.
    for tj in re.finditer(r"\((?:\\.|[^()\\])*\)\s*Tj", s):
        out.append(_pdf_literal(tj.group(0)))
    for tj in re.finditer(r"\[(.*?)\]\s*TJ", s, re.DOTALL):
        parts = re.findall(r"\((?:\\.|[^()\\])*\)", tj.group(1))
        out.append("".join(_pdf_literal(p) for p in parts))
    return " ".join(p for p in out if p)


def _pdf_literal(token: str) -> str:
    inner = token[token.find("(") + 1 : token.rfind(")")]
    inner = (
        inner.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
        .replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t")
    )
    return inner


def extract_text(filename: str, data: bytes) -> ExtractedText:
    """Recover prose from an uploaded file, dispatching on its extension.

    Unknown extensions fall back to a decode attempt (most real-world IR drafts
    are some flavour of text), with a warning so the caller knows it was a guess.
    """
    ext = _ext(filename or "")

    if ext == "docx":
        raw, warnings = _extract_docx(data)
        return ExtractedText(text=_tidy(raw), kind="docx", warnings=warnings)
    if ext == "pdf":
        raw, warnings = _extract_pdf(data)
        return ExtractedText(text=_tidy(raw), kind="pdf", warnings=warnings)
    if ext == "rtf":
        return ExtractedText(text=_tidy(_strip_rtf(_decode(data))), kind="rtf")
    if ext in _HTML_EXTS:
        return ExtractedText(text=_tidy(_strip_html(_decode(data))), kind="html")
    if ext in _TEXT_EXTS or ext == "":
        warnings = [] if ext else ["No file extension — read as plain text."]
        return ExtractedText(text=_tidy(_decode(data)), kind="text", warnings=warnings)

    # Unknown but possibly text (e.g. .docm, .pages export): decode and warn.
    return ExtractedText(
        text=_tidy(_decode(data)),
        kind="text",
        warnings=[f"Unrecognised file type '.{ext}' — read as plain text."],
    )
