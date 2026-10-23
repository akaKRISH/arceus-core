"""Command-line interface for ARCEUS core.

The CLI only validates arguments and formats results; all behavior lives in the
service modules so a future desktop UI can call the same code paths without
duplicating policy or storage logic.

Global options ``--db PATH`` and ``--json`` are accepted before or after the
subcommand. Use ``--`` to stop option parsing when a literal argument begins
with ``--``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from arceus_core import __version__
from arceus_core.config import default_data_dir, resolve_db_path
from arceus_core.database import open_database
from arceus_core.errors import ArceusError, RootError
from arceus_core.graph import (
    ancestors,
    children,
    graph_stats,
    lookup_entry,
    resolve_path,
    subtree,
)
from arceus_core.models import Entry, ScanSummary, SearchHit
from arceus_core.paths import display_path, is_within, path_key
from arceus_core.roots import (
    add_root,
    get_root,
    list_roots,
    remove_root,
)
from arceus_core.scanner import ScanOptions, scan_root
from arceus_core.search import SearchFilters, search
from arceus_core.status import collect_status, has_warnings

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


@dataclass(slots=True)
class _GlobalArgs:
    db: str | None = None
    json: bool = False
    version: bool = False


def _extract_globals(argv: list[str]) -> tuple[list[str], _GlobalArgs]:
    """Pull global options out of argv so they work before or after a command."""

    globals_ = _GlobalArgs()
    remaining: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--":
            remaining.extend(argv[index:])
            break
        if item == "--json":
            globals_.json = True
        elif item in ("--version", "-V"):
            globals_.version = True
        elif item == "--db":
            index += 1
            if index >= len(argv):
                raise ValueError("--db requires a path")
            globals_.db = argv[index]
        elif item.startswith("--db="):
            value = item[len("--db=") :]
            if not value:
                raise ValueError("--db requires a path")
            globals_.db = value
        else:
            remaining.append(item)
        index += 1
    return remaining, globals_


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arceus",
        description=(
            "ARCEUS core: metadata-only filesystem indexing and retrieval. "
            "Scans only explicitly registered roots and never reads file contents."
        ),
        epilog=(
            f"Default database: {default_data_dir() / 'arceus.sqlite3'} "
            f"(override with --db or ARCEUS_DATA_DIR)."
        ),
    )
    parser.add_argument(
        "--db", metavar="PATH", help="SQLite index path (default: per-user data dir)"
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--version", action="store_true", help="print the version and exit")

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    status_parser = sub.add_parser("status", help="show index health, roots, and scan state")
    status_parser.set_defaults(handler=_cmd_status)

    roots_parser = sub.add_parser("roots", help="manage explicitly registered indexing roots")
    roots_sub = roots_parser.add_subparsers(dest="roots_command", required=True, metavar="<action>")

    add_parser = roots_sub.add_parser("add", help="register an existing directory as an index root")
    add_parser.add_argument("path", help="directory to index (must exist)")
    add_parser.add_argument("--label", help="optional human-friendly label")
    add_parser.set_defaults(handler=_cmd_roots_add)

    list_parser = roots_sub.add_parser("list", help="list registered roots")
    list_parser.set_defaults(handler=_cmd_roots_list)

    remove_parser = roots_sub.add_parser(
        "remove", help="unregister a root and delete its index records"
    )
    remove_parser.add_argument("root", help="root id, registered path, or label")
    remove_parser.add_argument("--yes", action="store_true", help="confirm removal")
    remove_parser.set_defaults(handler=_cmd_roots_remove)

    scan_parser = sub.add_parser("scan", help="scan registered roots and reconcile the index")
    _add_scan_arguments(scan_parser)
    scan_parser.set_defaults(handler=_cmd_scan)

    index_parser = sub.add_parser("index", help="index operations (alias group)")
    index_sub = index_parser.add_subparsers(dest="index_command", required=True, metavar="<action>")
    index_scan = index_sub.add_parser("scan", help="scan registered roots and reconcile the index")
    _add_scan_arguments(index_scan)
    index_scan.set_defaults(handler=_cmd_scan)

    search_parser = sub.add_parser("search", help="search indexed file/directory names and paths")
    search_parser.add_argument("query", nargs="+", help="text to match (names and relative paths)")
    search_parser.add_argument("--root", help="restrict to a root id, path, or label")
    search_parser.add_argument("--ext", help="restrict to an extension, with or without a dot")
    search_parser.add_argument("--type", dest="entry_type", choices=("file", "dir"))
    search_parser.add_argument("--limit", type=int, default=20, help="maximum results (default 20)")
    search_parser.add_argument(
        "--include-missing",
        action="store_true",
        help="include entries not observed by the most recent scan",
    )
    search_parser.set_defaults(handler=_cmd_search)

    show_parser = sub.add_parser("show", help="show indexed metadata for one path")
    show_parser.add_argument("path", help="absolute path, or relative path with --root")
    show_parser.add_argument("--root", help="root id, path, or label for relative paths")
    show_parser.set_defaults(handler=_cmd_show)

    graph_parser = sub.add_parser("graph", help="query structural relationships in the index")
    graph_sub = graph_parser.add_subparsers(dest="graph_command", required=True, metavar="<action>")

    children_parser = graph_sub.add_parser("children", help="list direct children of a directory")
    children_parser.add_argument("path")
    children_parser.add_argument("--root")
    children_parser.add_argument("--include-missing", action="store_true")
    children_parser.set_defaults(handler=_cmd_graph_children)

    ancestors_parser = graph_sub.add_parser("ancestors", help="show the ancestor chain of a path")
    ancestors_parser.add_argument("path")
    ancestors_parser.add_argument("--root")
    ancestors_parser.set_defaults(handler=_cmd_graph_ancestors)

    tree_parser = graph_sub.add_parser(
        "tree", help="bounded breadth-first listing below a directory"
    )
    tree_parser.add_argument("path")
    tree_parser.add_argument("--root")
    tree_parser.add_argument("--depth", type=int, default=2, help="maximum depth (default 2)")
    tree_parser.set_defaults(handler=_cmd_graph_tree)

    stats_parser = graph_sub.add_parser("stats", help="entry counts and depth for a root")
    stats_parser.add_argument("--root")
    stats_parser.set_defaults(handler=_cmd_graph_stats)

    version_parser = sub.add_parser("version", help="print the version and exit")
    version_parser.set_defaults(handler=_cmd_version)

    return parser


def _add_scan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", help="scan only this root id, path, or label")
    parser.add_argument(
        "--max-entries",
        type=int,
        default=None,
        help="abort a root scan after this many observed entries (safety limit)",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")


def _configure_output_encoding() -> None:
    """Emit UTF-8 regardless of the redirected console code page.

    Windows pipes otherwise default to a legacy ANSI code page, which mangles
    non-ASCII paths in JSON and text output. ``backslashreplace`` guarantees a
    non-ASCII path can never crash output on an older console.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):  # pragma: no cover - unusual stream wrappers
            continue


def _json_out(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _fmt_mtime(mtime_ns: int | None) -> str:
    if mtime_ns is None:
        return "-"
    try:
        return datetime.fromtimestamp(mtime_ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "-"


def _entry_payload(entry: Entry, root_path: str) -> dict[str, Any]:
    return {
        "path": display_path(root_path, entry.rel_path),
        "rel_path": entry.rel_path,
        "name": entry.name,
        "type": entry.entry_type,
        "extension": entry.extension,
        "size_bytes": entry.size_bytes,
        "modified": _fmt_mtime(entry.mtime_ns),
        "status": entry.status,
        "root_id": entry.root_id,
        "first_seen_run_id": entry.first_seen_run_id,
        "last_seen_run_id": entry.last_seen_run_id,
        "observed_at": entry.observed_at,
    }


def _root_brief(root_id: int, path: str, label: str | None) -> str:
    suffix = f" ({label})" if label else ""
    return f"#{root_id} {path}{suffix}"


def _warning_lines(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #


def _cmd_status(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    if not db_path.exists():
        payload = {
            "version": __version__,
            "database": {"path": str(db_path), "exists": False},
            "roots": [],
            "totals": {"roots": 0, "present": 0, "missing": 0, "files": 0, "dirs": 0},
            "interrupted_or_running_scans": 0,
        }
        if as_json:
            _json_out(payload)
            return EXIT_OK
        print(f"ARCEUS {__version__}")
        print(f"database: {db_path} (not created yet)")
        print("roots: 0")
        print("next: arceus roots add <directory>  then  arceus scan")
        return EXIT_OK

    with open_database(db_path, create=False) as conn:
        report = collect_status(conn, str(db_path))

    if as_json:
        _json_out(report)
        return EXIT_OK

    db = report["database"]
    print(f"ARCEUS {report['version']}")
    print(
        f"database: {db['path']} "
        f"({_fmt_bytes(db['size_bytes'])}, schema v{db['schema_version']}, "
        f"integrity {'ok' if db['integrity_ok'] else 'FAILED'})"
    )
    totals = report["totals"]
    print(
        f"index: {totals['roots']} root(s), {totals['present']} present "
        f"({totals['files']} files, {totals['dirs']} dirs), {totals['missing']} missing"
    )
    if not report["roots"]:
        print("roots: none registered; run 'arceus roots add <directory>'")
    for root in report["roots"]:
        print()
        print(f"root {_root_brief(root['id'], root['path'], root['label'])} [{root['status']}]")
        entries = root["entries"]
        print(
            f"  entries: {entries['present']} present "
            f"({entries['files']} files, {entries['dirs']} dirs), {entries['missing']} missing"
        )
        scan = root["last_scan"]
        if scan is None:
            print("  last scan: never")
        else:
            print(
                f"  last scan: run #{scan['run_id']} {scan['status']} "
                f"at {scan['finished_at'] or scan['started_at']} "
                f"(files={scan['files_seen']} dirs={scan['dirs_seen']} "
                f"missing={scan['entries_marked_missing']} errors={scan['errors']} "
                f"skipped_symlinks={scan['skipped_symlinks']})"
            )
            if scan["notes"]:
                print(f"  note: {scan['notes']}")
    warnings = has_warnings(report)
    if warnings:
        print()
        _warning_lines(warnings)
    else:
        print()
        print("health: no warnings")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# roots
# --------------------------------------------------------------------------- #


def _is_broad_root(path: str) -> bool:
    key = path_key(path)
    home = path_key(Path.home())
    anchor = path_key(Path(path).anchor) if Path(path).anchor else ""
    return key == home or (anchor != "" and key == anchor)


def _cmd_roots_add(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    with open_database(db_path) as conn:
        root = add_root(conn, args.path, label=args.label)
    warnings = []
    if _is_broad_root(root.path):
        warnings.append(
            "this is a very broad root (home directory or drive root); "
            "ARCEUS will index every permitted entry beneath it"
        )
    if as_json:
        _json_out({"root": asdict(root), "warnings": warnings})
    else:
        print(f"registered root #{root.id}: {root.path}")
        if root.label:
            print(f"label: {root.label}")
        print("next: arceus scan --root " + str(root.id))
        _warning_lines(warnings)
    return EXIT_OK


def _cmd_roots_list(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    with open_database(db_path, create=False) as conn:
        roots = list_roots(conn)
        counts = {
            int(row["root_id"]): int(row["count"])
            for row in conn.execute(
                "SELECT root_id, COUNT(*) AS count FROM entries WHERE status = 'present' "
                "GROUP BY root_id"
            )
        }
    if as_json:
        _json_out(
            {
                "roots": [
                    {**asdict(root), "present_entries": counts.get(root.id, 0)} for root in roots
                ]
            }
        )
        return EXIT_OK
    if not roots:
        print("no roots registered; run 'arceus roots add <directory>'")
        return EXIT_OK
    for root in roots:
        label = f"  ({root.label})" if root.label else ""
        print(
            f"#{root.id}  {root.path}{label}  [{root.status}]  "
            f"{counts.get(root.id, 0)} present entries"
        )
    return EXIT_OK


def _cmd_roots_remove(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    if not args.yes:
        raise RootError(
            "refusing to remove a root and delete its index records without confirmation; "
            "re-run with --yes"
        )
    with open_database(db_path) as conn:
        root, entry_count = remove_root(conn, args.root)
    if as_json:
        _json_out({"removed": asdict(root), "deleted_entries": entry_count})
    else:
        print(f"removed root #{root.id}: {root.path}")
        print(f"deleted index records: {entry_count} (original files were not touched)")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #


def _exclusions_for_root(db_path: Path, root_path: str) -> tuple[str, ...]:
    candidates = [
        str(db_path),
        f"{db_path}-wal",
        f"{db_path}-shm",
    ]
    data_dir = str(db_path.parent)
    if is_within(data_dir, root_path) and path_key(data_dir) != path_key(root_path):
        candidates.append(data_dir)
    return tuple(candidates)


def _progress_printer(root_path: str):
    def report(stats) -> None:
        print(
            f"  scanning {root_path}: {stats.observed} entries observed, {stats.errors} errors",
            file=sys.stderr,
        )

    return report


def _summary_payload(summary: ScanSummary) -> dict[str, Any]:
    payload = asdict(summary)
    payload["error_samples"] = [asdict(item) for item in summary.error_samples]
    return payload


def _cmd_scan(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    with open_database(db_path) as conn:
        roots = [get_root(conn, args.root)] if args.root else list_roots(conn)
        if not roots:
            raise RootError("no roots registered; run 'arceus roots add <directory>'")

        summaries: list[ScanSummary] = []
        for root in roots:
            options = ScanOptions(
                exclude_paths=_exclusions_for_root(db_path, root.path),
                max_entries=args.max_entries,
            )
            progress = None if (as_json or args.quiet) else _progress_printer(root.path)
            summary = scan_root(conn, root, options, progress=progress)
            summaries.append(summary)
            if not as_json:
                print(
                    f"root #{root.id} {root.path}: {summary.status} "
                    f"(files={summary.files_seen} dirs={summary.dirs_seen} "
                    f"missing={summary.entries_marked_missing} errors={summary.errors} "
                    f"skipped_symlinks={summary.skipped_symlinks} "
                    f"{summary.duration_seconds:.3f}s, run #{summary.run_id})"
                )
                if summary.interrupted_runs_recovered:
                    print(
                        f"  recovered {summary.interrupted_runs_recovered} interrupted "
                        "scan run(s) from a previous process"
                    )
                for sample in summary.error_samples:
                    location = sample.rel_path or "(root)"
                    print(f"  error [{sample.kind}] {location}: {sample.message}", file=sys.stderr)

    if as_json:
        _json_out(
            {
                "summaries": [_summary_payload(summary) for summary in summaries],
                "failed": [summary.root_id for summary in summaries if summary.status == "failed"],
            }
        )
    return EXIT_ERROR if any(summary.status == "failed" for summary in summaries) else EXIT_OK


# --------------------------------------------------------------------------- #
# search / show
# --------------------------------------------------------------------------- #


def _hit_payload(hit: SearchHit) -> dict[str, Any]:
    return {
        "score": hit.score,
        "reason": hit.reason,
        **_entry_payload(hit.entry, hit.root_path),
    }


def _cmd_search(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    query = " ".join(args.query)
    with open_database(db_path, create=False) as conn:
        root_id = get_root(conn, args.root).id if args.root else None
        result = search(
            conn,
            query,
            SearchFilters(
                root_id=root_id,
                extension=args.ext,
                entry_type=args.entry_type,
                include_missing=args.include_missing,
                limit=args.limit,
            ),
        )

    if as_json:
        _json_out(
            {
                "query": result.query,
                "limit": result.limit,
                "count": len(result.hits),
                "warnings": result.warnings,
                "hits": [_hit_payload(hit) for hit in result.hits],
            }
        )
        return EXIT_OK

    print(f'{len(result.hits)} result(s) for "{result.query}"')
    for index, hit in enumerate(result.hits, start=1):
        marker = "f" if hit.entry.entry_type == "file" else "d"
        print(f"{index}. [{marker}] {hit.absolute_path}")
        print(
            f"   {_fmt_bytes(hit.entry.size_bytes)}  modified {_fmt_mtime(hit.entry.mtime_ns)}  "
            f"match: {hit.reason} (score {hit.score})  status: {hit.entry.status}"
        )
    _warning_lines(result.warnings)
    return EXIT_OK


def _cmd_show(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    with open_database(db_path, create=False) as conn:
        resolved = resolve_path(conn, args.path, root_identifier=args.root)
        entry = lookup_entry(conn, resolved.root.id, resolved.rel_path)
        chain = ancestors(conn, resolved)

    payload = {
        "path": resolved.absolute_path,
        "rel_path": resolved.rel_path,
        "root": {
            "id": resolved.root.id,
            "path": resolved.root.path,
            "label": resolved.root.label,
            "status": resolved.root.status,
        },
        "indexed": entry is not None,
        "entry": _entry_payload(entry, resolved.root.path) if entry is not None else None,
        "ancestry": [asdict(link) for link in chain],
        "evidence_note": "metadata only; file contents are never stored or searched",
    }
    if as_json:
        _json_out(payload)
        return EXIT_OK

    print(f"path: {resolved.absolute_path}")
    print(f"root: {_root_brief(resolved.root.id, resolved.root.path, resolved.root.label)}")
    if entry is None:
        print("index status: not indexed (path may not exist or the root needs a rescan)")
        print("evidence: none in the metadata index")
    else:
        print(f"type: {entry.entry_type}")
        if entry.entry_type == "file":
            print(f"size: {_fmt_bytes(entry.size_bytes)}")
        print(f"modified: {_fmt_mtime(entry.mtime_ns)}")
        print(f"index status: {entry.status}")
        print(
            f"observation: first seen run #{entry.first_seen_run_id}, "
            f"last seen run #{entry.last_seen_run_id} at {entry.observed_at}"
        )
        print("evidence: directory-entry metadata only; contents were not read")
    print("ancestry (root to parent):")
    for link in chain:
        origin = "observed" if link.observed else "inferred from path"
        print(f"  {link.display_name}  [{link.entry_type}, {origin}]")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# graph
# --------------------------------------------------------------------------- #


def _cmd_graph_children(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    with open_database(db_path, create=False) as conn:
        resolved = resolve_path(conn, args.path, root_identifier=args.root)
        entries = children(
            conn,
            resolved.root.id,
            resolved.rel_path,
            include_missing=args.include_missing,
        )

    if as_json:
        _json_out(
            {
                "directory": resolved.absolute_path,
                "root_id": resolved.root.id,
                "children": [_entry_payload(entry, resolved.root.path) for entry in entries],
            }
        )
        return EXIT_OK

    print(resolved.absolute_path)
    if not entries:
        print("  (no indexed children)")
        return EXIT_OK
    for entry in entries:
        marker = "d" if entry.entry_type == "dir" else "f"
        details = "" if entry.entry_type == "dir" else f"  {_fmt_bytes(entry.size_bytes)}"
        stale = "  [missing]" if entry.status == "missing" else ""
        print(f"  [{marker}] {entry.name}{details}{stale}")
    return EXIT_OK


def _cmd_graph_ancestors(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    with open_database(db_path, create=False) as conn:
        resolved = resolve_path(conn, args.path, root_identifier=args.root)
        chain = ancestors(conn, resolved)
        entry = lookup_entry(conn, resolved.root.id, resolved.rel_path)

    if as_json:
        _json_out(
            {
                "path": resolved.absolute_path,
                "root_id": resolved.root.id,
                "ancestry": [asdict(link) for link in chain],
                "entry": _entry_payload(entry, resolved.root.path) if entry is not None else None,
            }
        )
        return EXIT_OK

    print(f"ancestry of {resolved.absolute_path}:")
    for link in chain:
        origin = "observed" if link.observed else "inferred from path"
        print(f"  {link.rel_path or '.'}  [{link.entry_type}, {origin}]")
    if entry is None:
        print("target: not indexed")
    else:
        print(
            f"target: {entry.entry_type}, {entry.status}, last seen run #{entry.last_seen_run_id}"
        )
    return EXIT_OK


def _cmd_graph_tree(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    with open_database(db_path, create=False) as conn:
        resolved = resolve_path(conn, args.path, root_identifier=args.root)
        rows, truncated = subtree(conn, resolved.root.id, resolved.rel_path, max_depth=args.depth)

    if as_json:
        _json_out(
            {
                "directory": resolved.absolute_path,
                "root_id": resolved.root.id,
                "depth": args.depth,
                "truncated": truncated,
                "entries": [
                    {"depth": depth, **_entry_payload(entry, resolved.root.path)}
                    for depth, entry in rows
                ],
            }
        )
        return EXIT_OK

    print(resolved.absolute_path)
    for depth, entry in rows:
        marker = "d" if entry.entry_type == "dir" else "f"
        indent = "  " * depth
        print(f"{indent}[{marker}] {entry.name}")
    if truncated:
        print("(truncated)")
    if not rows:
        print("  (no indexed descendants)")
    return EXIT_OK


def _cmd_graph_stats(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    with open_database(db_path, create=False) as conn:
        roots = [get_root(conn, args.root)] if args.root else list_roots(conn)
        stats = [graph_stats(conn, root.id) for root in roots]

    payload = [
        {
            "root_id": root.id,
            "path": root.path,
            "status": root.status,
            "files": stat.files,
            "dirs": stat.dirs,
            "missing": stat.missing,
            "max_depth": stat.max_depth,
        }
        for root, stat in zip(roots, stats, strict=True)
    ]
    if as_json:
        _json_out({"roots": payload})
        return EXIT_OK
    if not payload:
        print("no roots registered")
        return EXIT_OK
    for item in payload:
        print(
            f"#{item['root_id']} {item['path']}: {item['files']} files, {item['dirs']} dirs, "
            f"{item['missing']} missing, max depth {item['max_depth']} [{item['status']}]"
        )
    return EXIT_OK


def _cmd_version(args: argparse.Namespace, db_path: Path, as_json: bool) -> int:
    if as_json:
        _json_out({"version": __version__})
    else:
        print(f"arceus {__version__}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    _configure_output_encoding()
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        remaining, globals_ = _extract_globals(raw)
    except ValueError as exc:
        print(f"arceus: error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if globals_.version:
        print(f"arceus {__version__}")
        return EXIT_OK

    parser = build_parser()
    try:
        args = parser.parse_args(remaining)
    except SystemExit as exc:  # help and usage errors
        code = exc.code
        return EXIT_OK if code is None else int(code)

    try:
        db_path = resolve_db_path(globals_.db)
        handler = getattr(args, "handler", None)
        if handler is None:  # pragma: no cover - argparse enforces subcommands
            parser.print_help()
            return EXIT_USAGE
        return int(handler(args, db_path, globals_.json))
    except ArceusError as exc:
        print(f"arceus: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except sqlite3.Error as exc:
        print(f"arceus: database error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        print(f"arceus: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("arceus: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
