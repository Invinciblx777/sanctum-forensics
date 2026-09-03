"""Post-erase verification and residual-risk assessment. Read-only.

Verification strategy depends on the method: a software overwrite can be
full-read verified, a firmware sanitize can only be hw-attested or sampled.
The residual-risk assessment states plainly what was not proven. Deferred to M1.
"""

from __future__ import annotations

from core.models import (
    DeviceCapabilities,
    EraseJob,
    ResidualRiskAssessment,
    VerificationResult,
)

__all__ = ["verify_erase", "assess_residual_risk"]


def verify_erase(
    job: EraseJob, *, strategy: str = "sampled"
) -> VerificationResult:
    """Read back the device per ``strategy`` and report whether it looks sanitized."""
    raise NotImplementedError


def assess_residual_risk(
    job: EraseJob,
    capabilities: DeviceCapabilities,
    verification: VerificationResult,
) -> ResidualRiskAssessment:
    """Summarise what could not be guaranteed for this job."""
    raise NotImplementedError
