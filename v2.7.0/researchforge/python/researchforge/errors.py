"""Failures that must be loud.

The failure mode this whole project is designed against is a system that reports
success it did not achieve. Every class here exists so that some specific way of
faking progress raises instead of returning.
"""
from __future__ import annotations


class ResearchForgeError(Exception):
    """Base."""


class ContractViolation(ResearchForgeError):
    """A skill read or wrote an artifact its contract does not permit.

    This is not a warning. The artifact graph is the system's only guarantee that
    a number in the manuscript came from a run that actually happened; a skill
    that writes outside its contract has silently broken that chain.
    """


class SchemaViolation(ResearchForgeError):
    """An artifact does not validate against its declared schema."""


class GateBlocked(ResearchForgeError):
    """A hard gate refused to let the run proceed. Not recoverable by retrying."""

    def __init__(self, gate: str, reason: str, remediation: str | None = None) -> None:
        self.gate, self.reason, self.remediation = gate, reason, remediation
        msg = f"gate '{gate}' blocked: {reason}"
        if remediation:
            msg += f"\n  remediation: {remediation}"
        super().__init__(msg)


class HumanDecisionRequired(ResearchForgeError):
    """Guided mode reached a decision only a person should make."""

    def __init__(self, prompt: str, options_artifact: str) -> None:
        self.prompt, self.options_artifact = prompt, options_artifact
        super().__init__(f"human decision required: {prompt}")


class NotImplementedYet(ResearchForgeError):
    """A skill that is specified but not built.

    Deliberately not `NotImplementedError`: this carries which batch of
    IMPLEMENTATION_PLAN.md would build it and what is missing, so that a run that
    hits an unbuilt stage says so precisely instead of degrading into a plausible
    fabrication.
    """

    def __init__(self, skill: str, batch: str, missing: str) -> None:
        self.skill, self.batch, self.missing = skill, batch, missing
        super().__init__(
            f"skill '{skill}' is specified but not implemented.\n"
            f"  would be built in: {batch}\n"
            f"  requires: {missing}\n"
            f"  This is a stub. It raises rather than returning a plausible result."
        )


class ProviderUnavailable(ResearchForgeError):
    """An external provider could not be reached, or its quota is exhausted."""


class CoverageInsufficient(ResearchForgeError):
    """A novelty judgment was attempted on a search that could not see enough.

    'We did not find prior work' and 'we could not look' must never be allowed to
    look the same.
    """
