// Turn an uploaded/pasted draft into a renderable library document.
//
// The backend's /analyze endpoint returns the recovered prose, the figure claims
// the edge proposed (with character spans), and the deterministic verdicts. This
// module assembles those into the same Block/Figure shapes the hand-authored demo
// documents use, so an upload renders — and ties out — exactly like the close pack.
//
// When no verification backend is reachable, `buildDocLocally` produces the same
// shapes from client-side numeric detection, with every figure marked untraced and
// an honest warning, so the upload still appears in the workspace.

import type {
  AnalyzeClaim,
  AnalyzeResult,
  AnalyzeVerdict,
} from "../api/client";
import { detectNewFigures } from "./verify";
import { ICON_FOR_KIND } from "../data/documents";
import type {
  Block,
  DocKind,
  DocVersion,
  Figure,
  Inline,
  VerdictState,
  VersionOrigin,
} from "../types";

const VERDICT_STATE: Record<string, VerdictState> = {
  traced: "v",
  needs_review: "r",
  conflict: "f",
  untraced: "u",
};

const BADGE: Record<VerdictState, string> = {
  v: "TRACED",
  r: "NEEDS SIGN-OFF",
  f: "CONFLICT",
  u: "UNTRACED",
};

const TAG: Record<VerdictState, string> = { v: "✓", r: "?", f: "!", u: "?" };

const SUBTITLE: Record<DocKind, string> = {
  release: "Earnings release · uploaded",
  script: "Earnings call script · uploaded",
  qa: "Q&A prep · uploaded",
  other: "Document · uploaded",
};

const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const prettyMetric = (m: string) => {
  const s = m.replace(/_/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
};

// A figure placed in the prose: where it sits, and the record behind it.
interface PlacedFigure {
  figureId: string;
  span: [number, number];
  figure: Figure;
}

function sourcePanel(
  lbl: string,
  metric: string,
  period: string,
  sourceValue: string | null,
  st: VerdictState
): string {
  const valueRow =
    sourceValue != null
      ? `<tr class="tot"><td>Source value</td><td class="num"><mark class="hl">${esc(sourceValue)}</mark></td></tr>`
      : `<tr class="tot"><td>Source value</td><td class="num">—</td></tr>`;
  const note =
    st === "u"
      ? "No filed source could be bound to this figure. Bind it or correct it before publish."
      : st === "r"
        ? "Bound to a non-filed source (e.g. forward guidance). Requires human sign-off."
        : st === "f"
          ? "Bound to a source, but the value differs. Reconcile before publish."
          : "Matched to a filed source within the tenant's rounding policy.";
  return (
    `<div class="filing"><div class="fhd">${esc(prettyMetric(metric))}</div>` +
    `<div class="fsub">${esc(period)}</div>` +
    `<table class="ftable"><tr class="head"><td>Metric</td><td class="num">Value</td></tr>` +
    `<tr><td>${esc(lbl)}</td><td class="num"></td></tr>` +
    valueRow +
    `</table><p class="note">${esc(note)}</p></div>`
  );
}

function makeFigure(
  figureId: string,
  claim: AnalyzeClaim | undefined,
  verdict: AnalyzeVerdict
): Figure {
  const st = VERDICT_STATE[verdict.verdict] ?? "u";
  const text = (claim?.displayed_text ?? verdict.displayed_text ?? "").trim();
  const filed = st === "v" || st === "f" ? verdict.source_value : null;
  const lbl = `${prettyMetric(verdict.metric)} · ${verdict.period}`;
  const cite =
    st === "v"
      ? `Traced to ${verdict.metric} (${verdict.period})`
      : st === "f"
        ? `Conflict · ${verdict.metric} (${verdict.period})`
        : st === "r"
          ? `Needs sign-off · ${verdict.metric}`
          : "No source bound yet";
  return {
    id: figureId,
    v: text,
    lbl,
    st,
    badge: BADGE[st],
    tag: TAG[st],
    cur: text,
    filed,
    editedFrom: null,
    snip: "",
    cite,
    page: sourcePanel(lbl, verdict.metric, verdict.period, verdict.source_value, st),
    reason: esc(verdict.reason || ""),
    fields: [
      { label: "As written", value: text || "—" },
      { label: "Source value", value: verdict.source_value ?? "—", tone: st === "f" ? "bad" : "" },
      { label: "Metric", value: prettyMetric(verdict.metric) },
      { label: "Period", value: verdict.period },
    ],
  };
}

// Resolve each verdict to a span in the prose (the claim's span if present, else
// the first occurrence of its text), drop overlaps, and keep document order.
// `scope` namespaces the figure ids so each version owns its own figures.
function placeFigures(
  scope: string,
  text: string,
  claims: AnalyzeClaim[],
  verdicts: AnalyzeVerdict[]
): PlacedFigure[] {
  const claimById = new Map(claims.map((c) => [c.claim_id, c]));
  const placed: PlacedFigure[] = [];
  for (const v of verdicts) {
    const claim = claimById.get(v.claim_id);
    let span = claim?.span ?? null;
    if (!span) {
      const needle = (claim?.displayed_text ?? v.displayed_text ?? "").trim();
      const idx = needle ? text.indexOf(needle) : -1;
      if (idx >= 0) span = [idx, idx + needle.length];
    }
    if (!span) continue; // can't anchor it in the prose; skip the inline token
    const figureId = `${scope}::${v.claim_id}`;
    placed.push({ figureId, span, figure: makeFigure(figureId, claim, v) });
  }
  placed.sort((a, b) => a.span[0] - b.span[0]);
  // Drop overlaps (keep the earlier one) so tokens never collide.
  const out: PlacedFigure[] = [];
  let cursor = -1;
  for (const p of placed) {
    if (p.span[0] >= cursor) {
      out.push(p);
      cursor = p.span[1];
    }
  }
  return out;
}

// Slice the prose into paragraphs, interleaving figure tokens at their spans.
function buildBlocks(
  title: string,
  subtitle: string,
  text: string,
  placed: PlacedFigure[]
): Block[] {
  const blocks: Block[] = [
    { kind: "eyebrow", text: subtitle },
    { kind: "h1", text: title },
    { kind: "hr" },
  ];

  // Paragraph boundaries (offsets) — split on blank lines.
  const bounds: { start: number; end: number }[] = [];
  const re = /\n{2,}/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    bounds.push({ start: last, end: m.index });
    last = m.index + m[0].length;
  }
  bounds.push({ start: last, end: text.length });

  for (const { start, end } of bounds) {
    if (!text.slice(start, end).trim()) continue;
    const figs = placed.filter((p) => p.span[0] >= start && p.span[0] < end);
    const parts: Inline[] = [];
    let cursor = start;
    for (const p of figs) {
      if (p.span[0] > cursor) parts.push({ kind: "text", html: esc(text.slice(cursor, p.span[0])) });
      parts.push({ kind: "fig", id: p.figureId });
      cursor = p.span[1];
    }
    if (cursor < end) parts.push({ kind: "text", html: esc(text.slice(cursor, end)) });
    blocks.push({ kind: "p", parts });
  }
  return blocks;
}

export function newDocId(): string {
  return `u_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

export function newVersionId(): string {
  return `v_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

const asDocKind = (k: string): DocKind =>
  (["release", "script", "qa", "other"].includes(k) ? k : "other") as DocKind;

/** Metadata a freshly built version contributes to its (possibly new) document. */
export interface VersionMeta {
  name: string;
  kind: DocKind;
  subtitle: string;
  icon: string;
  period: string;
}

/** One built version: the renderable version record, its figures (namespaced by
 *  the version id), and the document-level metadata it implies. The store decides
 *  whether to wrap this in a new document or append it to an existing one. */
export interface BuiltVersion {
  version: DocVersion;
  figures: Record<string, Figure>;
  meta: VersionMeta;
}

function assembleVersion(args: {
  versionId: string;
  origin: VersionOrigin;
  kind: DocKind;
  title: string;
  subtitle: string;
  text: string;
  claims: AnalyzeClaim[];
  verdicts: AnalyzeVerdict[];
  warnings?: string[];
  period: string;
}): BuiltVersion {
  const placed = placeFigures(args.versionId, args.text, args.claims, args.verdicts);
  const figures: Record<string, Figure> = {};
  for (const p of placed) figures[p.figureId] = p.figure;
  return {
    version: {
      id: args.versionId,
      label: "", // the store assigns the human label (it knows the version count)
      addedAt: new Date().toISOString(),
      origin: args.origin,
      blocks: buildBlocks(args.title, args.subtitle, args.text, placed),
      figureIds: placed.map((p) => p.figureId),
      warnings: args.warnings,
    },
    figures,
    meta: {
      name: args.title,
      kind: args.kind,
      subtitle: args.subtitle,
      icon: ICON_FOR_KIND[args.kind] ?? ICON_FOR_KIND.other,
      period: args.period,
    },
  };
}

/** Build a version from a real backend analysis. */
export function buildVersionFromAnalysis(
  result: AnalyzeResult,
  versionId: string
): BuiltVersion {
  const kind = asDocKind(result.kind);
  const title = result.title || "Uploaded document";
  return assembleVersion({
    versionId,
    origin: "upload",
    kind,
    title,
    subtitle: SUBTITLE[kind],
    text: result.text,
    claims: result.claims,
    verdicts: result.verdicts,
    warnings: result.warnings,
    period: result.period || "Uploaded",
  });
}

const REF_SUBTITLE: Record<DocKind, string> = {
  release: "Earnings release · loaded from the web",
  script: "Earnings call transcript · loaded from the web",
  qa: "Q&A · loaded from the web",
  other: "Document · loaded from the web",
};

/** Build a viewable version from a historical document fetched from the web.
 *
 *  These are *prior disclosures* loaded as the reference corpus a later draft is
 *  checked against — so the user can read the loaded release/transcript in the
 *  workspace instead of just seeing a figure count appear in the sidebar. Figures
 *  are detected and shown for reading; they read as untraced with a reference note
 *  (they're filed as reference facts on the backend, not verified as a draft is). */
export function buildReferenceVersion(
  input: { text: string; title: string; kind: DocKind; source?: string; period?: string },
  versionId: string
): BuiltVersion {
  const period = input.period || "—";
  const claims: AnalyzeClaim[] = [];
  const verdicts: AnalyzeVerdict[] = [];
  let i = 0;
  for (const figText of detectNewFigures(input.text)) {
    const id = `ref_${i++}`;
    claims.push({
      claim_id: id,
      metric: "reference_figure",
      period,
      entity: "—",
      displayed_text: figText,
      span: null,
    });
    verdicts.push({
      claim_id: id,
      metric: "reference_figure",
      period,
      displayed_text: figText,
      verdict: "untraced",
      reason:
        "Figure from a prior disclosure loaded as reference. It's filed as a reference fact your drafts tie out against — not verified as a draft itself.",
      source_value: null,
    });
  }
  return assembleVersion({
    versionId,
    origin: "upload",
    kind: input.kind,
    title: input.title || "Historical document",
    subtitle: REF_SUBTITLE[input.kind] ?? REF_SUBTITLE.other,
    text: input.text,
    claims,
    verdicts,
    warnings: input.source ? [`Loaded as reference from ${input.source}.`] : undefined,
    period: input.period || "Reference",
  });
}

/** Offline fallback: detect numeric spans client-side, mark them untraced, and
 *  attach an honest warning that no verification backend was reached. */
export function buildVersionLocally(
  input: { text: string; title: string; kind: DocKind; fromFile?: boolean },
  versionId: string
): BuiltVersion {
  const subtitle = SUBTITLE[input.kind] ?? SUBTITLE.other;
  const claims: AnalyzeClaim[] = [];
  const verdicts: AnalyzeVerdict[] = [];
  let i = 0;
  for (const figText of detectNewFigures(input.text)) {
    const id = `local_${i++}`;
    claims.push({
      claim_id: id,
      metric: "detected_figure",
      period: "—",
      entity: "—",
      displayed_text: figText,
      span: null,
    });
    verdicts.push({
      claim_id: id,
      metric: "detected_figure",
      period: "—",
      displayed_text: figText,
      verdict: "untraced",
      reason:
        "Detected in the draft. No verification backend is connected, so this figure could not be tied out to a filed source.",
      source_value: null,
    });
  }
  return assembleVersion({
    versionId,
    origin: input.fromFile ? "upload" : "paste",
    kind: input.kind,
    title: input.title || "Uploaded document",
    subtitle,
    text: input.text,
    claims,
    verdicts,
    warnings: [
      "No verification backend connected — figures were detected but not tied out to a filed source.",
    ],
    period: "Uploaded",
  });
}
