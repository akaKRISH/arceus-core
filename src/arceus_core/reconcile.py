"""Conservative reconciliation of entries that were not observed in a scan.

The rule is evidence-based:

* An indexed entry is marked ``missing`` only if its *immediate parent directory
  was fully enumerated* during the scan and the entry was not seen.
* If a directory was unreadable, its children are never marked missing, because
  the scan has no evidence about them.
* Descendants of a directory that was observed to be gone are also marked
  missing, in a bounded cascade. This is an inference from the observed removal
  of the parent, and it still requires the parent itself to have been observed
  absent under a fully enumerated grandparent.

Rows are never deleted by reconciliation. They keep ``first_seen_run_id`` and
``last_seen_run_id`` so history and staleness remain inspectable, and a later
scan that observes the entry again flips it back to ``present``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

_DIRECT_SQL = """
UPDATE entries
SET status = 'missing'
WHERE root_id = :root_id
  AND status = 'present'
  AND last_seen_run_id != :run_id
  AND parent_rel_key IN (SELECT rel_key FROM _scan_complete_dirs)
"""

_CASCADE_SQL = """
UPDATE entries
SET status = 'missing'
WHERE root_id = :root_id
  AND status = 'present'
  AND last_seen_run_id != :run_id
  AND parent_rel_key IN (
      SELECT rel_key
      FROM entries
      WHERE root_id = :root_id AND status = 'missing' AND entry_type = 'dir'
  )
"""

_MAX_CASCADE_ROUNDS = 128


def reconcile_missing(
    conn: sqlite3.Connection,
    root_id: int,
    run_id: int,
    complete_dir_keys: Iterable[str],
) -> int:
    """Mark entries missing when this scan provides evidence they are gone.

    Returns the number of rows transitioned to ``missing``.
    """

    keys = sorted(set(complete_dir_keys))

    with conn:
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _scan_complete_dirs (rel_key TEXT PRIMARY KEY)"
        )
        conn.execute("DELETE FROM _scan_complete_dirs")
        if keys:
            conn.executemany(
                "INSERT OR IGNORE INTO _scan_complete_dirs (rel_key) VALUES (?)",
                ((key,) for key in keys),
            )

        params = {"root_id": root_id, "run_id": run_id}
        direct = conn.execute(_DIRECT_SQL, params)
        marked = direct.rowcount if direct.rowcount and direct.rowcount > 0 else 0

        for _ in range(_MAX_CASCADE_ROUNDS):
            cascade = conn.execute(_CASCADE_SQL, params)
            if not cascade.rowcount or cascade.rowcount <= 0:
                break
            marked += cascade.rowcount

    return marked
