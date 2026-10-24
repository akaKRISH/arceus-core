"""Independent outcome verification for executed read-only actions.

The verifier is deliberately separate from the model and from the executor:
it never trusts the proposal's rationale, the executor's status, or the
model's claims. It re-derives correctness from the ORIGINAL task text, the
validated action, and the ACTUAL tool result, using deterministic checks and
the same metadata index (never file contents).

Verification answers two distinct questions:

* ``verification_status`` — does the observable evidence support the result
  the executor reported? (verified / unverified / incomplete)
* ``task_completed`` — did the requested condition hold? Terminal decisions
  (clarify/abstain) are checked against deterministic index facts, not the
  model's story. Uncertainty stays uncertainty: stale or missing evidence
  yields ``unverified`` rather than a forced success.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from arceus_core.controller import (
    ANCESTOR_MARKERS,
    STRUCTURAL_MARKERS,
    UNSAFE_MARKERS,
    _search_query,
    classify_task,
)
from arceus_core.executor import ExecutionResult
from arceus_core.search import SearchFilters, search

_VERIFICATION_UNCERTAIN_MARKERS = (
    "stale",
    "incomplete",
    "never been scanned",
    "marked missing",
    "interrupted",
)

VerificationStatus = str  # "verified" | "unverified" | "incomplete"


def _task_is_listing(task: str) -> bool:
    """Directory-listing request, deterministic: structural hints, no
    ancestor words."""

    lowered = task.casefold()
    return any(marker in lowered for marker in STRUCTURAL_MARKERS) and not any(
        marker in lowered for marker in ANCESTOR_MARKERS
    )


def _task_is_ancestry(task: str) -> bool:
    lowered = task.casefold()
    return any(marker in lowered for marker in ANCESTOR_MARKERS)


def verify_execution(
    task: str,
    result: ExecutionResult,
    *,
    conn: sqlite3.Connection | None = None,
) -> tuple[VerificationStatus, str, bool | None]:
    """Independently verify one executed (or terminal) decision.

    Returns ``(verification_status, reason, task_completed)``.
    ``task_completed`` is None when correctness cannot be established.
    """

    if result.proposed_action == "ask_clarification":
        return _check_clarify(task, conn)
    if result.proposed_action == "abstain":
        return _check_abstain(task, conn)

    if result.status == "rejected":
        return "verified", f"safe rejection: {result.failure_reason}", False
    if result.status == "failed":
        return "unverified", f"execution failed: {result.failure_reason}", False

    evidence = result.evidence
    warnings = result.warnings

    if result.proposed_action == "search_files":
        return _check_search(task, result, evidence, warnings)
    if result.proposed_action == "graph_children":
        if conn is None:
            return "unverified", "graph children verification requires the index connection", None
        return _check_children(task, result, evidence, warnings)
    if result.proposed_action == "graph_ancestors":
        if conn is None:
            return "unverified", "graph ancestors verification requires the index connection", None
        return _check_ancestors(task, result, evidence, warnings)
    return "unverified", f"no verification rule for action {result.proposed_action!r}", None


def _downgrade_for_warnings(status: str, verdict: str, warnings: list[str]) -> tuple[str, str]:
    """Stale/missing index evidence downgrades certainty, never fabricates it."""

    stale = [
        warning
        for warning in warnings
        if any(marker in warning for marker in _VERIFICATION_UNCERTAIN_MARKERS)
    ]
    if stale:
        reason = f"{verdict}; index warnings: {stale[0]}"
        return ("unverified" if status == "verified" else status), reason
    return status, verdict


def _check_search(
    task: str,
    result: ExecutionResult,
    evidence: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[str, str, bool | None]:
    """A search is correct only if reported hits genuinely satisfy the query.

    Zero legitimate hits must NOT be reported as a positive discovery; for a
    genuinely no-result task an empty, well-formed result IS accurate, and
    the task-level score decides completion.

    Classification uses the task INSTRUCTION (first line), matching how the
    controller routes the task: untrusted metadata lines must not change how
    the verifier reads the request.
    """

    instruction = task.splitlines()[0] if task else ""
    if classify_task(instruction) == "content_request":
        # A task asking what a document SAYS can never be completed from a
        # metadata-only index, even if the file itself was located.
        return (
            "incomplete",
            "task requires document contents; the index stores metadata only",
            False,
        )
    if classify_task(instruction) != "name_search":
        return (
            "unverified",
            "search executed for a request the classifier does not read as name search",
            None,
        )

    query = str((result.tool_result or {}).get("query", "")).strip().casefold()
    if not query:
        return "unverified", "search result has no query recorded", None

    for item in evidence:
        name = str(item.get("name", "")).casefold()
        rel_path = str(item.get("rel_path", "")).casefold()
        if query not in name and query not in rel_path:
            return (
                "unverified",
                f"reported hit {item.get('rel_path')!r} does not satisfy the query",
                None,
            )

    hit_count = int((result.tool_result or {}).get("hit_count", len(evidence)))
    if hit_count > len(evidence) and not (result.tool_result or {}).get("truncated_evidence"):
        return "unverified", "hit count exceeds recorded evidence", None

    if hit_count == 0:
        status, reason = _downgrade_for_warnings(
            "verified", "accurate empty result (no matches in the metadata index)", warnings
        )
        return status, reason, None

    status, reason = _downgrade_for_warnings(
        "verified",
        f"all {hit_count} reported hit(s) satisfy the query and registry scope",
        warnings,
    )
    return status, reason, True


def _check_children(
    task: str,
    result: ExecutionResult,
    evidence: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[str, str, bool | None]:
    """Direct-children proof: every reported entry must name the requested
    path as its parent (no deeper descendants) and carry the same root."""

    tool = result.tool_result or {}
    if not _task_is_listing(task):
        return (
            "unverified",
            "children executed for a request the classifier does not read as a directory listing",
            None,
        )

    root_id = tool.get("root_id")
    path = str(tool.get("path", ""))
    for item in evidence:
        parent = str(item.get("parent_rel_path", "")).casefold()
        item_rel = str(item.get("rel_path", ""))
        if parent != path.casefold():
            return "unverified", f"entry {item_rel!r} is not a direct child of {path!r}", None
        if int(item.get("root_id", -1)) != int(root_id):
            return "unverified", f"entry {item_rel!r} came from a different root", None

    if tool.get("requested_path_status") in (None, "missing"):
        return (
            "incomplete",
            f"requested directory {path!r} is not (or no longer) indexed as present",
            False,
        )

    status, reason = _downgrade_for_warnings(
        "verified",
        f"all reported entries are direct children of the indexed directory {path!r}",
        warnings,
    )
    return status, reason, True


def _check_ancestors(
    task: str,
    result: ExecutionResult,
    evidence: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[str, str, bool | None]:
    """Ancestor proof: the chain starts at the registered root and every link
    must be an actual path-prefix of the reported target."""

    tool = result.tool_result or {}
    if not _task_is_ancestry(task):
        return (
            "unverified",
            "ancestors executed for a request the classifier does not read as an ancestor lookup",
            None,
        )

    path = str(tool.get("path", ""))
    if not evidence:
        return "incomplete", "no ancestor links were reported", False

    first = evidence[0]
    if first.get("rel_path") != "" or first.get("observed") is not True:
        return "unverified", "ancestor chain must start at the registered root", None

    parts = [part for part in path.split("/") if part]
    links = evidence[1:]
    if len(links) != max(0, len(parts) - 1):
        return "unverified", "ancestor chain length does not match the target path depth", None
    for index, link in enumerate(links):
        expected_prefix = "/".join(parts[: index + 1])
        if str(link.get("rel_path", "")) != expected_prefix:
            return (
                "unverified",
                f"ancestor link {index} should be {expected_prefix!r}, "
                f"got {link.get('rel_path')!r}",
                None,
            )

    if tool.get("target_status") in (None, "missing"):
        return (
            "incomplete",
            f"target path {path!r} is not (or no longer) indexed in the registry",
            False,
        )

    status, reason = _downgrade_for_warnings(
        "verified", f"ancestor chain of {path!r} matches the observed index exactly", warnings
    )
    return status, reason, True


def _check_clarify(task: str, conn: sqlite3.Connection | None) -> tuple[str, str, bool | None]:
    """Clarification is verified against index facts: the request must be
    genuinely ambiguous (>= 2 distinct matching entries in the index)."""

    if conn is None:
        return "unverified", "clarification verification requires the index connection", None
    needle = _search_query(task)
    if not needle:
        return (
            "unverified",
            "clarification proposed, but the task contains no extractable name query",
            None,
        )
    result = search(conn, needle, SearchFilters(limit=100))
    distinct_paths = len({hit.entry.rel_path for hit in result.hits})
    if distinct_paths >= 2:
        return (
            "verified",
            f"clarification is supported: {distinct_paths} distinct entries match the request",
            True,
        )
    return (
        "unverified",
        f"clarification proposed, but the index shows {distinct_paths} matching entry/entries",
        None,
    )


def _check_abstain(task: str, conn: sqlite3.Connection | None) -> tuple[str, str, bool | None]:
    """Abstention is verified against index facts only:

    * unsafe/unsupported request -> correct abstention (verified);
    * name/path request with ZERO matching entries -> correct (verified);
    * matching entries exist -> abstention withheld evidence (unverified).
    """

    kind = classify_task(task)
    if kind == "unsafe_request":
        return "verified", "unsafe/unsupported request correctly abstained", True
    if kind == "invalid":
        return "verified", "unusable request correctly abstained", True
    if kind == "content_request":
        # Abstention is the correct decision, but the requested knowledge was
        # NOT obtained: the task itself stays incomplete.
        return (
            "verified",
            "content question correctly abstained: metadata-only index cannot answer it",
            False,
        )

    if conn is None:
        return "unverified", "abstention verification requires the index connection", None
    lowered = task.casefold()
    if any(marker in lowered for marker in UNSAFE_MARKERS):
        return "verified", "unsafe/unsupported request correctly abstained", True

    # Name/structural request: is there ACTUALLY no matching evidence?
    needle = _search_query(task) or task
    result = search(conn, needle[:100], SearchFilters(limit=100))
    if not result.hits:
        return "verified", "insufficient index evidence: no entries match the request", True
    return (
        "unverified",
        f"abstained although {len(result.hits)} matching entr(ies) exist in the index",
        None,
    )
