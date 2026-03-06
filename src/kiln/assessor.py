"""assessor.py — shim for backward compatibility.

All types and functions now live in kiln.agents.assessor.
"""

from kiln.agents.assessor import (  # noqa: F401
    CONFIDENCE_UPGRADE_THRESHOLD,
    AllocationResult,
    Allocator,
    Assessor,
    ComplexityEstimate,
    ComplexityTier,
    _upgrade_tier,
    assess_and_allocate,
    assess_from_plan_artifact,
)
