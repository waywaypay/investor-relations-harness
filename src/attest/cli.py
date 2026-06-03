"""Command-line entry point.

    attest demo     # ingest the Meridian filing, verify the close pack, print a report
    attest eval     # run the golden-set gates and print a scored report
    attest serve    # run the API with uvicorn
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


def _run_eval() -> int:
    """Run every golden-set gate and print a scored report. Non-zero on a breach.

    This is the same gate ``tests/test_eval.py`` asserts, exposed as a command so
    eval is a usable QA tool — run it locally before a model/rule/prompt change, or
    as an explicit step in CI — not only a pytest assertion.
    """
    from attest.eval import run_gates

    gates = run_gates()
    print("Attest — eval gates\n" + "=" * 56)
    for gate in gates:
        status = "PASS" if gate.passed else "FAIL"
        print(f"\n[{status}] {gate.name}")
        for key, value in gate.metrics.items():
            if isinstance(value, list):
                print(f"   {key}: {len(value)}")
                for item in value:
                    print(f"      - {item}")
            else:
                print(f"   {key}: {value}")

    passed = all(gate.passed for gate in gates)
    print("\n" + "=" * 56)
    print(f"-> {'all gates passed' if passed else 'GATE FAILURE — see above'}")
    return 0 if passed else 1


def _run_serve(host: str, port: int) -> int:
    import uvicorn

    from attest.api.app import create_app

    print(f"Attest — upload & verify UI at http://{host}:{port}  (API docs at /docs)")
    uvicorn.run(create_app(), host=host, port=port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="attest", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="verify the bundled Meridian close pack")
    sub.add_parser("eval", help="run the golden-set gates and print a scored report")
    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    if args.command == "demo":
        return _run_demo()
    if args.command == "eval":
        return _run_eval()
    if args.command == "serve":
        return _run_serve(args.host, args.port)
    return 1


if __name__ == "__main__":
    sys.exit(main())
