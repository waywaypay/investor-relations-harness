"""Command-line entry point.

    attest demo     # ingest the Meridian filing, verify the close pack, print a report
    attest serve    # run the API with uvicorn
    attest synth    # generate synthetic perturbation cases (robustness coverage)
"""

from __future__ import annotations

import argparse
import sys

from attest.demo import build_documents, seeded_service
from attest.domain.verdicts import Verdict

_GLYPH = {
    Verdict.TRACED: "[traced]   ",
    Verdict.NEEDS_REVIEW: "[review]   ",
    Verdict.CONFLICT: "[CONFLICT] ",
    Verdict.UNTRACED: "[untraced] ",
}


def _run_demo() -> int:
    service = seeded_service()
    documents = build_documents()
    results, consistency = service.verify_close_pack(documents)

    print("Attest — Meridian Systems Q1 FY2026 close pack\n" + "=" * 56)
    for result in results:
        doc = next(d for d in documents if d.id == result.document_id)
        c = result.counts
        print(f"\n## {doc.title}")
        print(
            f"   traced {c['traced']} · review {c['needs_review']} · "
            f"conflict {c['conflict']} · untraced {c['untraced']}"
        )
        for v in result.verdicts:
            print(f"   {_GLYPH[v.verdict]}{v.metric:<22} {v.displayed_text:<22} {v.reason}")
        for f in result.findings:
            print(f"   [{f.severity.value}] {f.rule}: {f.message}")
        print(f"   -> publishable: {result.publishable}")

    if consistency:
        print("\n## Cross-document consistency")
        for f in consistency:
            print(f"   [{f.severity.value}] {f.message}\n      {f.detail}")
    else:
        print("\n## Cross-document consistency: all figures agree across documents")

    print("\n## Audit trail")
    print(f"   {len(service.audit_log.events())} events · chain intact: {service.audit_verify()}")
    return 0


def _run_serve(host: str, port: int) -> int:
    import uvicorn

    from attest.api.app import create_app

    uvicorn.run(create_app(), host=host, port=port)
    return 0


def _run_synth(csv_path: str | None, out_path: str | None) -> int:
    import json

    from attest.eval.perturbation import perturb_facts
    from attest.eval.synthetic_eval import run_synthetic_eval

    if csv_path:
        from attest.eval.sheets_bridge import facts_from_csv_path

        facts = facts_from_csv_path(csv_path, tenant_id="corpus")
        cases = perturb_facts(facts)
        print(f"Generated {len(cases)} synthetic cases from {csv_path}")
    else:
        from attest.ingestion.edgar_xbrl import load_fixture
        from attest.service import AttestService

        svc = AttestService()
        svc.ingest_xbrl(load_fixture("meridian_q1_fy2026"), tenant_id="meridian")
        cases = perturb_facts(svc.store.all("meridian"))
        print(f"Generated {len(cases)} synthetic cases from the bundled Meridian fixture")

    if out_path:
        payload = {
            "name": "synthetic_perturbation",
            "label_source": "synthetic_perturbation",
            "caveat": "Robustness coverage only — NOT a reliability metric. Keep "
            "separate from the human-labeled golden gate.",
            "cases": [c.as_golden_row() for c in cases],
        }
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"Wrote {out_path}")

    # When driving from the bundled fixture we can also score the engine on them.
    if not csv_path:
        report = run_synthetic_eval()
        d = report.as_dict()
        print(f"\nBucket: {d['bucket']}")
        print(f"  cases {d['total']} · accuracy {d['exact_accuracy']} · "
              f"FN rate {d['figure_false_negative_rate']}")
        print(f"  {d['caveat']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="attest", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="verify the bundled Meridian close pack")
    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    synth = sub.add_parser(
        "synth", help="generate synthetic perturbation cases (robustness coverage)"
    )
    synth.add_argument("--csv", help="path to a 02_Facts CSV export; omit to use the fixture")
    synth.add_argument("--out", help="write generated cases to this JSON path")

    args = parser.parse_args(argv)
    if args.command == "demo":
        return _run_demo()
    if args.command == "serve":
        return _run_serve(args.host, args.port)
    if args.command == "synth":
        return _run_synth(args.csv, args.out)
    return 1


if __name__ == "__main__":
    sys.exit(main())
