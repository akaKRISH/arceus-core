"""Phase 6: bounded, local-only episodic memory over completed tasks.

Design rules (see SPRINT_STATUS Phase 6 and the phase report):

* Storage reuses the project SQLite database through an additive, versioned
  migration (schema.py migration 2). Nothing leaves the machine: no cloud
  database, no embedding service, no telemetry.
* Records are minimal and normalized: a task *signature* (first instruction
  line, case-folded, whitespace-collapsed) — never raw prompts, file
  contents, absolute paths, or credentials. Evidence is stored only as
  root-relative ``(root_id, rel_path)`` references to already-indexed rows.
* Retrieval is deterministic: exact-signature match first, then task
  category, then token overlap, newest first — documented ranking, no vector
  embeddings.
* Trust rules are structural: only an ACTIVE episode with outcome
  ``verified_success`` (independently verified, task completed) is ever
  ``authoritative``. Unverified and failed episodes are returned only as
  warnings/failure examples. Adoption additionally re-confirms every evidence
  reference against the CURRENT index (``confirm_evidence``); a stale episode
  is rejected rather than replayed. The workflow never lets memory bypass
  controller validation, executor permission checks, or the verifier.
* Repeated duplicates of the same outcome collapse into one episode row with
  an ``occurrence_count``, so copies cannot inflate evidence counts.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from arceus_core.controller import READ_ONLY_ACTIONS, classify_task
from arceus_core.errors import MemoryStoreError

MEMORY_POLICY_VERSION = "memory-policy-v1"
MAX_SIGNATURE_CHARS = 300
MAX_EVIDENCE_REFS = 10
MAX_JSON_CHARS = 2000

Outcome = Literal["verified_success", "unverified", "failure"]
EpisodeStatus = Literal["active", "corrected", "invalidated", "expired"]

_VERIFICATION_VALUES = frozenset({"verified", "unverified", "incomplete"})
# Any absolute filesystem path or URL appearing in stored reason text is
# replaced so memory never retains sensitive locations.
_ABS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s'\"]+|/(?:home|Users|tmp|var)/[^\s'\"]+")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_task_signature(task: str) -> str:
    """Normalized, bounded signature of the task instruction (first line).

    Metadata lines below the 'Untrusted metadata:' marker are excluded so
    untrusted text cannot shape retrieval keys.
    """

    text = task.splitlines()[0] if task else ""
    normalized = " ".join(text.casefold().split()).rstrip(".,!?;:")
    if len(normalized) > MAX_SIGNATURE_CHARS:
        normalized = normalized[:MAX_SIGNATURE_CHARS].rstrip()
    return normalized


def _scrub_text(text: str) -> str:
    return _ABS_PATH_RE.sub("[path]", text)[:300]


def _safe_evidence_refs(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only bounded, root-relative (root_id, rel_path) pairs."""

    refs: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        root_id = item.get("root_id")
        rel_path = item.get("rel_path")
        if not isinstance(root_id, int) or not isinstance(rel_path, str):
            continue
        normalized = rel_path.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            continue
        refs.append({"root_id": root_id, "rel_path": normalized})
        if len(refs) >= MAX_EVIDENCE_REFS:
            break
    return refs


def derive_outcome(
    *, task_completed: bool | None, executed: bool, verification_status: str | None
) -> Outcome:
    """Explicit promotion rule: only an independently established completed
    outcome becomes ``verified_success``; unknown outcomes stay ``unverified``."""

    if task_completed is True and (not executed or verification_status == "verified"):
        return "verified_success"
    if task_completed is False:
        return "failure"
    return "unverified"


@dataclass(frozen=True, slots=True)
class Episode:
    """One recorded (deduplicated) episode, with explicit trust fields."""

    id: int
    created_at: str
    last_seen_at: str
    task_signature: str
    task_category: str
    action: str
    origin: str
    executed: bool
    verification_status: str | None
    task_completed: bool | None
    outcome: Outcome
    failure_category: str | None
    evidence_refs: tuple[dict[str, Any], ...]
    provenance: dict[str, Any]
    config_version: str
    skill_id: str | None
    status: EpisodeStatus
    correction_of: int | None
    occurrence_count: int

    @property
    def authoritative(self) -> bool:
        """Verified knowledge only: active + verified_success."""

        return self.status == "active" and self.outcome == "verified_success"

    @property
    def warning(self) -> str | None:
        if self.status != "active":
            return f"episode_{self.status}"
        if self.outcome == "unverified":
            return "unverified_outcome"
        if self.outcome == "failure":
            return "failed_outcome"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "task_signature": self.task_signature,
            "task_category": self.task_category,
            "action": self.action,
            "origin": self.origin,
            "executed": self.executed,
            "verification_status": self.verification_status,
            "task_completed": self.task_completed,
            "outcome": self.outcome,
            "failure_category": self.failure_category,
            "evidence_refs": [dict(ref) for ref in self.evidence_refs],
            "provenance": dict(self.provenance),
            "config_version": self.config_version,
            "skill_id": self.skill_id,
            "status": self.status,
            "correction_of": self.correction_of,
            "occurrence_count": self.occurrence_count,
            "authoritative": self.authoritative,
            "warning": self.warning,
        }


@dataclass(frozen=True, slots=True)
class RetrievedEpisode:
    """An episode plus retrieval rank and match explanation."""

    episode: Episode
    rank: int
    matched: str  # exact_signature | category | token_overlap
    authoritative: bool
    warning: str | None


def _row_to_episode(row: sqlite3.Row) -> Episode:
    try:
        evidence = json.loads(row["evidence_refs"])
        provenance = json.loads(row["provenance"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise MemoryStoreError(f"corrupt episode record id={row['id']}: {exc}") from exc
    if not isinstance(evidence, list) or not isinstance(provenance, dict):
        raise MemoryStoreError(f"corrupt episode record id={row['id']}")
    return Episode(
        id=int(row["id"]),
        created_at=str(row["created_at"]),
        last_seen_at=str(row["last_seen_at"]),
        task_signature=str(row["task_signature"]),
        task_category=str(row["task_category"]),
        action=str(row["action"]),
        origin=str(row["origin"]),
        executed=bool(row["executed"]),
        verification_status=row["verification_status"],
        task_completed=None if row["task_completed"] is None else bool(row["task_completed"]),
        outcome=row["outcome"],
        failure_category=row["failure_category"],
        evidence_refs=tuple(evidence),
        provenance=provenance,
        config_version=str(row["config_version"]),
        skill_id=row["skill_id"],
        status=row["status"],
        correction_of=row["correction_of"],
        occurrence_count=int(row["occurrence_count"]),
    )


class EpisodicMemory:
    """Typed local store: record, retrieve, inspect, correct, delete, retain."""

    def __init__(self, conn: sqlite3.Connection, *, retention_limit: int = 500) -> None:
        if retention_limit < 1:
            raise MemoryStoreError("retention_limit must be >= 1")
        self._conn = conn
        self._retention_limit = retention_limit

    # -- recording ----------------------------------------------------------

    def record(
        self,
        task: str,
        *,
        action: str,
        origin: str,
        executed: bool,
        verification_status: str | None,
        task_completed: bool | None,
        category: str | None = None,
        failure_category: str | None = None,
        evidence: list[dict[str, Any]] | None = None,
        provenance: dict[str, Any] | None = None,
        config_version: str = "unknown",
        skill_id: str | None = None,
    ) -> Episode:
        """Validate and store one completed-task episode (deduplicated)."""

        signature = normalize_task_signature(task)
        if not signature:
            raise MemoryStoreError("cannot record an episode for an empty task signature")
        if action not in READ_ONLY_ACTIONS:
            raise MemoryStoreError(f"action {action!r} is outside the read-only allow-list")
        if origin not in {"model", "deterministic", "memory", "skill"}:
            raise MemoryStoreError(f"unknown origin {origin!r}")
        if verification_status is not None and verification_status not in _VERIFICATION_VALUES:
            raise MemoryStoreError(f"unknown verification status {verification_status!r}")
        if not isinstance(executed, bool) or (
            task_completed is not None and not isinstance(task_completed, bool)
        ):
            raise MemoryStoreError("executed/task_completed must be booleans or None")

        resolved_category = category or classify_task(task.splitlines()[0] if task else "")
        outcome = derive_outcome(
            task_completed=task_completed,
            executed=executed,
            verification_status=verification_status,
        )
        # A rejected/failed record can never become verified knowledge even if
        # a caller passes an optimistic task_completed flag.
        if executed and verification_status != "verified" and task_completed is True:
            raise MemoryStoreError(
                "cannot record a completed executed task that was not independently verified"
            )

        refs = _safe_evidence_refs(evidence or [])
        prov: dict[str, Any] = {}
        for key, value in (provenance or {}).items():
            if not isinstance(key, str):
                continue
            if key == "arguments" and isinstance(value, dict):
                # Keep only contract arguments (scalars), never derived
                # absolute paths, bounded in size and scrubbed of locations.
                clean_args: dict[str, Any] = {}
                for arg_key, arg_value in list(value.items())[:8]:
                    if arg_key in {"root_id", "absolute_path"}:
                        continue
                    if isinstance(arg_value, str):
                        clean_args[arg_key] = _scrub_text(arg_value)[:200]
                    elif isinstance(arg_value, (int, float, bool)):
                        clean_args[arg_key] = arg_value
                prov["arguments"] = clean_args
            elif isinstance(value, str):
                prov[key] = _scrub_text(value)
            elif isinstance(value, (int, float, bool)) or value is None:
                prov[key] = value
        if len(json.dumps(prov, ensure_ascii=False)) > MAX_JSON_CHARS:
            prov = {"truncated": True}

        dedupe_key = hashlib.sha256(
            "|".join([signature, resolved_category, action, outcome, config_version]).encode(
                "utf-8"
            )
        ).hexdigest()

        now = _utc_now()
        existing = self._conn.execute(
            "SELECT id, occurrence_count FROM episodes WHERE dedupe_key = ? AND status = 'active'",
            (dedupe_key,),
        ).fetchone()
        if existing is not None:
            self._conn.execute(
                "UPDATE episodes SET occurrence_count = occurrence_count + 1, last_seen_at = ? "
                "WHERE id = ?",
                (now, existing["id"]),
            )
            self._conn.commit()
            episode = self.get(int(existing["id"]))
            assert episode is not None
            return episode

        cursor = self._conn.execute(
            "INSERT INTO episodes (created_at, last_seen_at, task_signature, task_category, "
            "action, origin, executed, verification_status, task_completed, outcome, "
            "failure_category, evidence_refs, provenance, config_version, skill_id, status, "
            "correction_of, occurrence_count, dedupe_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, 1, ?)",
            (
                now,
                now,
                signature,
                resolved_category,
                action,
                origin,
                int(executed),
                verification_status,
                None if task_completed is None else int(task_completed),
                outcome,
                failure_category,
                json.dumps(refs, ensure_ascii=False),
                json.dumps(prov, ensure_ascii=False),
                config_version,
                skill_id,
                dedupe_key,
            ),
        )
        self._conn.commit()
        episode = self.get(int(cursor.lastrowid))
        assert episode is not None
        self.apply_retention()
        return episode

    def record_from_workflow_result(self, result: Any, **kwargs: Any) -> Episode:
        """Record a WorkflowResult with honest, explicit provenance fields."""

        instruction = result.task.splitlines()[0] if result.task else ""
        provenance = {
            "task_id": result.task_id,
            "origin": str(result.origin),
            "fallback_used": bool(result.fallback_used),
            "rejection_stage": result.rejection_stage,
            "verification_reason": result.verification_reason,
            "wall_seconds": result.wall_seconds,
            "arguments": dict(result.proposed_arguments),
        }
        if result.reuse_decision:
            provenance["reuse_decision"] = result.reuse_decision
        return self.record(
            result.task,
            action=result.proposed_action,
            origin=str(result.origin),
            executed=bool(result.executed),
            verification_status=result.verification_status,
            task_completed=result.task_completed,
            category=kwargs.pop("category", None) or classify_task(instruction),
            failure_category=kwargs.pop("failure_category", None),
            evidence=result.evidence,
            provenance=provenance,
            config_version=kwargs.pop("config_version", "unknown"),
            skill_id=kwargs.pop("skill_id", None),
            **kwargs,
        )

    # -- retrieval ----------------------------------------------------------

    def retrieve(
        self, task: str, *, category: str | None = None, limit: int = 5
    ) -> list[RetrievedEpisode]:
        """Deterministic ranking over ACTIVE episodes.

        Score: 4 for an exact normalized-signature match, 2 for a category
        match (when a category is supplied), plus token Jaccard overlap
        (rounded). Ties break newest-first (highest id first).
        """

        if limit < 1:
            raise MemoryStoreError("limit must be >= 1")
        signature = normalize_task_signature(task)
        signature_tokens = set(signature.split())
        resolved_category = category or classify_task(task.splitlines()[0] if task else "")

        rows = self._conn.execute(
            "SELECT * FROM episodes WHERE status = 'active' ORDER BY id DESC LIMIT 2000"
        ).fetchall()

        scored: list[tuple[float, str, Episode]] = []
        for row in rows:
            try:
                episode = _row_to_episode(row)
            except MemoryStoreError:
                continue  # a corrupt row never participates in retrieval
            score = 0.0
            matched = "token_overlap"
            if episode.task_signature == signature:
                score += 4.0
                matched = "exact_signature"
            if category is not None and episode.task_category == resolved_category:
                score += 2.0
                if matched != "exact_signature":
                    matched = "category"
            episode_tokens = set(episode.task_signature.split())
            union = signature_tokens | episode_tokens
            if union:
                score += round(len(signature_tokens & episode_tokens) / len(union), 4)
            if score <= 0:
                continue
            scored.append((score, matched, episode))

        scored.sort(key=lambda item: (-item[0], -item[2].id))
        results: list[RetrievedEpisode] = []
        for rank, (_score, matched, episode) in enumerate(scored[:limit], start=1):
            results.append(
                RetrievedEpisode(
                    episode=episode,
                    rank=rank,
                    matched=matched,
                    authoritative=episode.authoritative,
                    warning=episode.warning,
                )
            )
        return results

    def confirm_evidence(self, episode: Episode) -> tuple[bool, list[str]]:
        """Re-confirm every evidence reference against the CURRENT index.

        A verified episode whose evidence disappeared (renamed/deleted file,
        rescanned root) is stale: current observations win over memory.
        """

        if not episode.evidence_refs:
            return False, ["episode has no evidence references to re-confirm"]
        from arceus_core.graph import lookup_entry  # local import: avoid import cycle

        problems: list[str] = []
        for ref in episode.evidence_refs:
            root_row = self._conn.execute(
                "SELECT status FROM roots WHERE id = ?", (ref["root_id"],)
            ).fetchone()
            if root_row is None:
                problems.append(f"root {ref['root_id']} no longer registered")
                continue
            if str(root_row["status"]) != "active":
                problems.append(f"root {ref['root_id']} is marked missing")
                continue
            entry = lookup_entry(self._conn, ref["root_id"], ref["rel_path"])
            if entry is None:
                problems.append(f"{ref['rel_path']} is no longer in the index")
            elif entry.status != "present":
                problems.append(f"{ref['rel_path']} is marked missing")
        return (not problems), problems

    # -- inspection / lifecycle --------------------------------------------

    def get(self, episode_id: int) -> Episode | None:
        row = self._conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if row is None:
            return None
        try:
            return _row_to_episode(row)
        except MemoryStoreError:
            return None

    def mark_corrected(self, episode_id: int) -> Episode:
        """Supersede an episode: excluded from retrieval, still inspectable."""

        if self.get(episode_id) is None:
            raise MemoryStoreError(f"no episode id={episode_id}")
        self._conn.execute("UPDATE episodes SET status = 'corrected' WHERE id = ?", (episode_id,))
        self._conn.commit()
        episode = self.get(episode_id)
        assert episode is not None
        return episode

    def mark_invalidated(self, episode_id: int) -> Episode:
        if self.get(episode_id) is None:
            raise MemoryStoreError(f"no episode id={episode_id}")
        self._conn.execute("UPDATE episodes SET status = 'invalidated' WHERE id = ?", (episode_id,))
        self._conn.commit()
        episode = self.get(episode_id)
        assert episode is not None
        return episode

    def delete(self, episode_id: int) -> bool:
        cursor = self._conn.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def apply_retention(self) -> int:
        """Enforce the retention limit: evict the oldest rows beyond it."""

        total = int(self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0])
        excess = total - self._retention_limit
        if excess <= 0:
            return 0
        cursor = self._conn.execute(
            "DELETE FROM episodes WHERE id IN (SELECT id FROM episodes ORDER BY id ASC LIMIT ?)",
            (excess,),
        )
        self._conn.commit()
        return int(cursor.rowcount)

    def stats(self) -> dict[str, Any]:
        conn = self._conn
        by_outcome = {
            str(row["outcome"]): int(row["n"])
            for row in conn.execute(
                "SELECT outcome, COUNT(*) AS n FROM episodes GROUP BY outcome ORDER BY outcome"
            )
        }
        by_status = {
            str(row["status"]): int(row["n"])
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM episodes GROUP BY status ORDER BY status"
            )
        }
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(occurrence_count), 0) AS occurrences FROM episodes"
        ).fetchone()
        dedupe_hits = int(row["occurrences"]) - int(row["n"])
        return {
            "policy_version": MEMORY_POLICY_VERSION,
            "episode_count": int(row["n"]),
            "occurrence_total": int(row["occurrences"]),
            "deduplicated_duplicates": dedupe_hits,
            "by_outcome": by_outcome,
            "by_status": by_status,
            "authoritative_count": int(
                conn.execute(
                    "SELECT COUNT(*) FROM episodes "
                    "WHERE status = 'active' AND outcome = 'verified_success'"
                ).fetchone()[0]
            ),
            "retention_limit": self._retention_limit,
            "skill_count": int(conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]),
            "skill_run_count": int(conn.execute("SELECT COUNT(*) FROM skill_runs").fetchone()[0]),
        }
