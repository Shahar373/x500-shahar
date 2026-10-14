"""Check status values, check results and overall-status aggregation.

Rules enforced here (and covered by tests):

* Missing, stale, unsupported or malformed evidence is UNKNOWN, never PASS.
* A critical check that is UNKNOWN (including "threshold/expected value not
  configured") makes the overall result INCOMPLETE - never "passed".
* Automatic checks passing says nothing about the manual checklist and does
  not guarantee a safe flight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Overall(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    INCOMPLETE = "INCOMPLETE"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    category: str
    title: str
    status: Status
    critical: bool
    explanation: str
    observed: str | None = None
    expected: str | None = None
    source: str = ""
    age_s: float | None = None
    configured: bool = True
    limitations: str | None = None
    missing: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    observed_at: str | None = None   # local wall-clock time of the observation (laptop clock)
    evaluated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "category": self.category,
            "title": self.title,
            "status": self.status.value,
            "critical": self.critical,
            "explanation": self.explanation,
            "observed": self.observed,
            "expected": self.expected,
            "source": self.source,
            "age_s": None if self.age_s is None else round(self.age_s, 2),
            "observed_at": self.observed_at,
            "evaluated_at": self.evaluated_at,
            "configured": self.configured,
            "limitations": self.limitations,
            "missing": list(self.missing),
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class OverallResult:
    status: Overall
    counts: dict[str, int]
    blocking: tuple[str, ...]  # check ids that prevent PASS
    critical_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "counts": self.counts,
            "blocking": list(self.blocking),
            "critical_count": self.critical_count,
        }


def aggregate(results: Iterable[CheckResult]) -> OverallResult:
    results = list(results)
    counts = {s.value: 0 for s in Status}
    for r in results:
        counts[r.status.value] += 1
    if not results:
        return OverallResult(Overall.NOT_RUN, counts, (), 0)

    critical = [r for r in results if r.critical]
    crit_fail = [r.check_id for r in critical if r.status is Status.FAIL]
    crit_unknown = [r.check_id for r in critical if r.status is Status.UNKNOWN]
    # A critical check that claims PASS while marked unconfigured is a bug in
    # the check; treat it as incomplete rather than trusting it.
    crit_unconfigured = [
        r.check_id for r in critical if not r.configured and r.status is not Status.FAIL
    ]

    if crit_fail:
        return OverallResult(Overall.FAIL, counts, tuple(crit_fail), len(critical))
    blocking = tuple(dict.fromkeys(crit_unknown + crit_unconfigured))
    if blocking:
        return OverallResult(Overall.INCOMPLETE, counts, blocking, len(critical))
    if not any(r.status is Status.PASS for r in critical):
        # Nothing critical was actually verified (e.g. everything N/A).
        return OverallResult(Overall.INCOMPLETE, counts, (), len(critical))
    issues = [
        r.check_id
        for r in results
        if r.status in (Status.WARN, Status.FAIL, Status.UNKNOWN)
    ]
    if issues:
        return OverallResult(Overall.PASS_WITH_WARNINGS, counts, (), len(critical))
    return OverallResult(Overall.PASS, counts, (), len(critical))
