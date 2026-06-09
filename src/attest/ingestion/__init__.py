"""Source connectors. One adapter per source; the fact store is the core."""

from attest.ingestion.base import Connector, IngestionReport
from attest.ingestion.edgar_releases import EdgarReleaseConnector
from attest.ingestion.edgar_xbrl import XBRLConnector, load_fixture
from attest.ingestion.exa_releases import ExaReleaseFetcher
from attest.ingestion.releases import EarningsRelease, ReleaseFetchReport

__all__ = [
    "Connector",
    "EarningsRelease",
    "EdgarReleaseConnector",
    "ExaReleaseFetcher",
    "IngestionReport",
    "ReleaseFetchReport",
    "XBRLConnector",
    "load_fixture",
]
