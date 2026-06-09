"""Command-line entry point.

    attest demo                  # ingest the Meridian filing, verify the close pack
    attest serve                 # run the API with uvicorn
    attest releases META         # fetch the last 4 quarterly earnings releases (EDGAR)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def _run_releases(args: argparse.Namespace) -> int:
    """Fetch the last N quarterly earnings releases and show what was recovered.

    The figure count per release is printed deliberately: an earnings release
    whose text contains no figures is the wrong artifact (an advisory or a
    shell page), and that failure should be visible at fetch time, not at
    verification time.
    """
    if args.source == "edgar":
        from attest.ingestion.edgar_releases import EdgarReleaseConnector

        connector = EdgarReleaseConnector(user_agent=args.user_agent)
        releases, report = connector.fetch_quarterly(args.issuer, quarters=args.quarters)
    else:
        from attest.ingestion.exa_releases import ExaReleaseFetcher

        releases, report = ExaReleaseFetcher().fetch_quarterly(
            args.issuer, quarters=args.quarters
        )

    print(f"Fetched {report.fetched}/{report.requested} quarterly releases via {report.source}")
    for release in releases:
        print(
            f"  {release.period or '?':<10} {release.title[:58]:<58} "
            f"figures {release.figure_count:>4}  chars {len(release.text):>7}"
        )
        print(f"             {release.url}")
        for warning in release.warnings:
            print(f"             warning: {warning}")
    for note in report.missing:
        print(f"  MISSING    {note}")

    if args.out:
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        for release in releases:
            name = f"{release.entity}-{release.period or release.accession or 'unknown'}.txt"
            (outdir / name).write_text(release.text, encoding="utf-8")
        print(f"Wrote {len(releases)} file(s) to {outdir}/ — ready for `attest serve` upload")
    return 0 if report.fetched == report.requested else 1


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
    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    releases = sub.add_parser(
        "releases", help="fetch the last N quarterly earnings press releases"
    )
    releases.add_argument(
        "issuer", help="ticker or CIK (edgar) / company name as titled in releases (exa)"
    )
    releases.add_argument("--quarters", type=int, default=4)
    releases.add_argument(
        "--source",
        choices=("edgar", "exa"),
        default="edgar",
        help="edgar (deterministic, authoritative — default) or exa (needs EXA_API_KEY)",
    )
    releases.add_argument("--out", default=None, help="directory to write recovered text into")
    releases.add_argument(
        "--user-agent",
        default=None,
        help="SEC fair-access User-Agent: 'name contact@email' (or ATTEST_SEC_USER_AGENT)",
    )

    args = parser.parse_args(argv)
    if args.command == "demo":
        return _run_demo()
    if args.command == "serve":
        return _run_serve(args.host, args.port)
    if args.command == "releases":
        return _run_releases(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
