"""Deterministic rules layered on top of figure verification.

These are pure functions over the document, its claims, and the fact store. They
never call a model. Each returns a list of :class:`RuleFinding`.
"""

from attest.verification.rules.consistency import check_cross_document_consistency
from attest.verification.rules.derived import check_derived_consistency
from attest.verification.rules.forward_looking import check_forward_looking
from attest.verification.rules.reg_g import check_reg_g

__all__ = [
    "check_reg_g",
    "check_forward_looking",
    "check_cross_document_consistency",
    "check_derived_consistency",
]
