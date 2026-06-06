"""System prompts and tool schemas for the LLM edge.

Kept in one place so they are easy to review and cache. The prompts encode the
*only* job the model has — locate candidate figures and read narrative direction —
and explicitly deny it the one thing it must never do: declare a tie-out. Every
schema is built to be forced via ``tool_choice`` so the model returns structured
data, never prose we have to parse.
"""

from __future__ import annotations

PROPOSER_SYSTEM = (
    "You are the figure-detection edge of a disclosure-verification system for "
    "investor-relations teams. Your only job is to LOCATE every numeric figure a "
    "draft asserts and PROPOSE which canonical metric, entity, and fiscal period "
    "each one is claiming. You do NOT decide whether a figure is correct, traced, "
    "or reconciled — a separate deterministic engine does that. Over-detection is "
    "acceptable; missing a figure is not. When you are unsure which metric a span "
    "maps to, still report the span and set confidence to 'low' so a human reviews "
    "it. Copy each figure's text exactly as written, including the currency symbol, "
    "scale word, and sign."
)

REPORT_FIGURES_TOOL = {
    "name": "report_figures",
    "description": (
        "Report every numeric figure found in the draft, mapped to a candidate "
        "metric/entity/period. Report a span even when the metric is uncertain "
        "(use confidence='low')."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "figures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "displayed_text": {
                            "type": "string",
                            "description": "the figure exactly as written, e.g. '$1.24 billion'",
                        },
                        "metric": {
                            "type": "string",
                            "description": "candidate canonical metric id from the vocabulary",
                        },
                        "entity": {
                            "type": "string",
                            "description": "issuer or segment, e.g. 'ATLS' or 'ATLS:Cloud'",
                        },
                        "period": {
                            "type": "string",
                            "description": "fiscal period, e.g. 'FY2026-Q1'",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "how sure you are of the metric mapping",
                        },
                        "span_start": {
                            "type": "integer",
                            "description": "character offset where the figure starts (optional)",
                        },
                        "span_end": {
                            "type": "integer",
                            "description": "character offset where the figure ends (optional)",
                        },
                    },
                    "required": ["displayed_text", "metric", "entity", "period", "confidence"],
                },
            }
        },
        "required": ["figures"],
    },
}

NARRATOR_SYSTEM = (
    "You are the historical-consistency reviewer for an investor-relations "
    "disclosure. You are given draft prose and a table of verified historical "
    "figures across recent periods. Flag passages whose NARRATIVE implication "
    "contradicts the historical numbers — for example prose that says growth is "
    "'accelerating' when year-over-year growth is in fact decelerating, or "
    "'record' / 'strongest ever' claims the history does not support. You never "
    "judge whether an individual figure ties out (another system does that); you "
    "only catch wording whose direction or superlative the numbers refute. Be "
    "conservative: a false alarm erodes trust, so only flag a passage when the "
    "contradiction is clear from the supplied figures. If nothing is "
    "contradictory, return an empty list."
)

REPORT_NARRATIVE_FLAGS_TOOL = {
    "name": "report_narrative_flags",
    "description": (
        "Report passages whose narrative implication contradicts the supplied "
        "historical figures. Return an empty list if none."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "flags": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "passage": {
                            "type": "string",
                            "description": "the exact prose passage at issue",
                        },
                        "metric": {
                            "type": "string",
                            "description": "the metric whose history the passage contradicts",
                        },
                        "claimed_implication": {
                            "type": "string",
                            "description": "what the wording implies, e.g. 'accelerating growth'",
                        },
                        "contradiction": {
                            "type": "string",
                            "description": "how the historical figures contradict it",
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["warn", "info"],
                            "description": "warn for clear contradictions, info for soft ones",
                        },
                    },
                    "required": ["passage", "claimed_implication", "contradiction", "severity"],
                },
            }
        },
        "required": ["flags"],
    },
}
