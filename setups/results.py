"""Shared result shape for every setups/ pipeline stage.

Code-review finding: UniverseResult aside, ValidationResult/FilterResult/
CandidateResult were three independently-defined dataclasses with an
identical (passed, reason_code) shape -- consolidated here so a future
field only needs to be added once.
"""
from dataclasses import dataclass


@dataclass
class Result:
    passed: bool
    reason_code: str | None = None
