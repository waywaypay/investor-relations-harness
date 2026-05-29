"""The deterministic verification engine: detect -> normalize -> bind -> verdict."""

from attest.verification.candidates import Candidate, detect_candidates
from attest.verification.engine import VerificationEngine, VerificationResult

__all__ = [
    "Candidate",
    "detect_candidates",
    "VerificationEngine",
    "VerificationResult",
]
