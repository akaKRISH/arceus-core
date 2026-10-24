"""Typed abstention reasons and their escalation class (Phase 7.1).

Phase 7 routed *any* deterministic abstention to the model, which conflated a
correct terminal answer ("no such entry exists") with a recoverable
interpretation gap ("evidence exists but the phrasing did not map to it").
This module makes that distinction explicit data instead of free text.

Rules:

* A reason is **terminal** when additional interpretation alone cannot establish
  correctness: unsafe requests, content the metadata index cannot answer,
  missing evidence, out-of-scope or unindexed paths, exhausted resources, or
  reuse layers that are unavailable/revoked/failed.
* A reason is **recoverable** only when permitted read-only evidence exists and
  the deterministic planner failed to map the phrasing to a supported
  operation. Exactly one code currently qualifies: ``unmapped_intent``.
* Anything unknown, missing, or malformed is **not** recoverable. Callers must
  fail closed: no model call.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

EscalationClass = Literal["terminal", "recoverable", "unknown"]


class AbstentionReason(StrEnum):
    """Every way the system can abstain, typed.

    Controller-level reasons describe a deterministic proposal; the remaining
    entries describe why the router itself refused a strategy.
    """

    # -- deterministic controller abstentions ---------------------------------
    INVALID_TASK = "invalid_task"
    UNSAFE_REQUEST = "unsafe_request"
    CONTENT_UNAVAILABLE = "content_unavailable"
    NO_SEARCH_TARGET = "no_search_target"
    SEARCH_TARGET_INVALID = "search_target_invalid"
    NO_INDEXED_EVIDENCE = "no_indexed_evidence"
    NO_INDEXED_PATH = "no_indexed_path"
    PATH_NOT_IN_SCOPE = "path_not_in_scope"
    PATH_NOT_INDEXED = "path_not_indexed"
    #: The only recoverable class: a supported read-only operation exists and
    #: index evidence exists, but the deterministic planner could not map the
    #: user's phrasing to it.
    UNMAPPED_INTENT = "unmapped_intent"
    # -- router-level refusals -------------------------------------------------
    UNKNOWN_ABSTENTION_REASON = "unknown_abstention_reason"
    MODEL_UNAVAILABLE = "model_unavailable"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    RESOURCES_UNKNOWN = "resources_unknown"
    BUDGET_EXHAUSTED = "budget_exhausted"


TERMINAL_REASONS: frozenset[AbstentionReason] = frozenset(
    {
        AbstentionReason.INVALID_TASK,
        AbstentionReason.UNSAFE_REQUEST,
        AbstentionReason.CONTENT_UNAVAILABLE,
        AbstentionReason.NO_SEARCH_TARGET,
        AbstentionReason.SEARCH_TARGET_INVALID,
        AbstentionReason.NO_INDEXED_EVIDENCE,
        AbstentionReason.NO_INDEXED_PATH,
        AbstentionReason.PATH_NOT_IN_SCOPE,
        AbstentionReason.PATH_NOT_INDEXED,
        AbstentionReason.UNKNOWN_ABSTENTION_REASON,
        AbstentionReason.MODEL_UNAVAILABLE,
        AbstentionReason.RESOURCE_EXHAUSTED,
        AbstentionReason.RESOURCES_UNKNOWN,
        AbstentionReason.BUDGET_EXHAUSTED,
    }
)

RECOVERABLE_REASONS: frozenset[AbstentionReason] = frozenset({AbstentionReason.UNMAPPED_INTENT})

#: Reasons that can only be raised by the router (never by a Proposal).
ROUTER_ONLY_REASONS: frozenset[AbstentionReason] = frozenset(
    {
        AbstentionReason.UNKNOWN_ABSTENTION_REASON,
        AbstentionReason.MODEL_UNAVAILABLE,
        AbstentionReason.RESOURCE_EXHAUSTED,
        AbstentionReason.RESOURCES_UNKNOWN,
        AbstentionReason.BUDGET_EXHAUSTED,
    }
)


def coerce_reason(value: object) -> AbstentionReason | None:
    """Return the enum member for ``value`` or ``None`` when it is not one.

    Comparisons are exact (enum or its string value). A malformed, unknown, or
    missing reason yields ``None`` so callers can fail closed instead of
    guessing an escalation class from a prefix or substring.
    """

    if isinstance(value, AbstentionReason):
        return value
    if isinstance(value, str):
        try:
            return AbstentionReason(value)
        except ValueError:
            return None
    return None


def escalation_class(reason: object) -> EscalationClass:
    """Classify a reason for routing. Unknown values are never recoverable."""

    member = coerce_reason(reason)
    if member is None:
        return "unknown"
    if member in RECOVERABLE_REASONS:
        return "recoverable"
    if member in TERMINAL_REASONS:
        return "terminal"
    return "unknown"


def is_recoverable(reason: object) -> bool:
    """True only for a recognized, explicitly recoverable reason."""

    return escalation_class(reason) == "recoverable"


__all__ = [
    "AbstentionReason",
    "EscalationClass",
    "RECOVERABLE_REASONS",
    "ROUTER_ONLY_REASONS",
    "TERMINAL_REASONS",
    "coerce_reason",
    "escalation_class",
    "is_recoverable",
]
