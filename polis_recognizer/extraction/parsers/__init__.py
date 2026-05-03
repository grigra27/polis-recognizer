"""Field parser registry.

Registration order is also extraction order — the pipeline iterates
this list. The legacy fields come first so their results land in
the same shape consumers already understand; new fields follow and
end up in ``additional_fields``.
"""

from .base import ExtractionContext, FieldParser
from .franchise import FranchiseParser
from .limit import LimitParser
from .policy_number import PolicyNumberParser
from .policy_period import PolicyPeriodParser
from .premium import PremiumParser
from .repair_mode import RepairModeParser
from .sum_type import SumTypeParser


# Order matters: the legacy fields preserve API shape; v2-only
# fields (``additional_fields``) appear after them.
LEGACY_PARSERS = (
    PolicyPeriodParser(),
    FranchiseParser(),
    LimitParser(),
    RepairModeParser(),
)

ADDITIONAL_PARSERS = (
    PremiumParser(),
    SumTypeParser(),
    PolicyNumberParser(),
)

ALL_PARSERS = LEGACY_PARSERS + ADDITIONAL_PARSERS

__all__ = [
    "ExtractionContext",
    "FieldParser",
    "ALL_PARSERS",
    "LEGACY_PARSERS",
    "ADDITIONAL_PARSERS",
]
