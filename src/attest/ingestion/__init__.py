"""Source connectors. One adapter per source; the fact store is the core."""

from attest.ingestion.base import Connector, IngestionReport
from attest.ingestion.edgar_xbrl import XBRLConnector, load_fixture

__all__ = ["Connector", "IngestionReport", "XBRLConnector", "load_fixture"]
