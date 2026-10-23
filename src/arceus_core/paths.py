"""Path normalization helpers with explicit, documented platform semantics.

Two different notions of identity are used deliberately:

* ``normalize_abs`` / ``path_key`` identify a *root directory* on the host.
  On Windows ``os.path.normcase`` lower-cases and converts separators, so
  ``C:\\Data`` and ``c:\\data`` are the same root. On POSIX they are distinct.
* ``rel_key_for`` identifies an *entry within a root*. Paths stored in the
  index use forward slashes on every platform. On Windows the key is
  case-folded because the common Windows filesystems are case-insensitive;
  on POSIX the key preserves case because they are case-sensitive.

The key choice is made from the host OS at scan time. A database created on
Windows and opened on Linux therefore needs a rescan (documented limitation).
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath


def normalize_abs(path: str | os.PathLike[str]) -> str:
    """Return an absolute, normalized (but not symlink-resolved) path string."""

    expanded = os.path.expanduser(os.fspath(path))
    if not expanded:
        raise ValueError("path must not be empty")
    return os.path.normpath(os.path.abspath(expanded))


def path_key(path: str | os.PathLike[str]) -> str:
    """Return a comparison key for an absolute path (case-insensitive on Windows)."""

    return os.path.normcase(normalize_abs(path))


def is_within(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> bool:
    """True when ``path`` is ``root`` itself or a descendant of it."""

    child_key = path_key(path)
    root_key = path_key(root)
    if child_key == root_key:
        return True
    return child_key.startswith(root_key + os.sep)


def relative_to_root(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> str:
    """Return ``path`` relative to ``root`` in forward-slash form ('' for the root)."""

    relative = os.path.relpath(normalize_abs(path), normalize_abs(root))
    if relative == ".":
        return ""
    parts = [part for part in relative.replace("\\", "/").split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError(f"{path!s} is outside root {root!s}")
    return "/".join(parts)


def rel_key_for(rel_path: str) -> str:
    """Return the case-normalized key for a root-relative path."""

    return rel_path.casefold() if os.name == "nt" else rel_path


def rel_parent(rel_path: str) -> str:
    """Return the parent of a relative path, '' for a top-level entry."""

    index = rel_path.rfind("/")
    return "" if index < 0 else rel_path[:index]


def join_rel(parent: str, name: str) -> str:
    """Join a parent relative path and a child name with '/'."""

    return name if parent == "" else f"{parent}/{name}"


def rel_depth(rel_path: str) -> int:
    """Number of path segments in a relative path ('' has depth 0)."""

    return 0 if rel_path == "" else rel_path.count("/") + 1


def is_descendant_rel(rel_path: str, ancestor: str) -> bool:
    """True when ``rel_path`` is strictly below ``ancestor`` ('' means root)."""

    if ancestor == "":
        return rel_path != ""
    return rel_path.startswith(ancestor + "/")


def extension_of(name: str) -> str:
    """Return the lower-case extension without a dot, or '' when there is none."""

    return PurePosixPath(name).suffix.lower().lstrip(".")


def to_posix_rel(parts: list[str] | tuple[str, ...]) -> str:
    """Join path segments into a forward-slash relative path."""

    return "/".join(part for part in parts if part not in ("", "."))


def display_path(root_path: str, rel_path: str) -> str:
    """Reconstruct a native absolute path for display/open-in-OS use."""

    if rel_path == "":
        return root_path
    return os.path.join(root_path, *rel_path.split("/"))


def safe_name(name: str) -> str:
    """Reject names that cannot belong to a single path entry."""

    if name in ("", ".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"invalid entry name: {name!r}")
    return name


def path_basename(path: str) -> str:
    """Basename that tolerates trailing separators on every platform."""

    stripped = os.path.normpath(path)
    return Path(stripped).name
