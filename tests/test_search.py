from __future__ import annotations

import os
from pathlib import Path

import pytest

from arceus_core.database import open_database
from arceus_core.errors import QueryError
from arceus_core.roots import add_root
from arceus_core.scanner import scan_root
from arceus_core.search import SearchFilters, search


@pytest.fixture
def search_root(conn, root_dir: Path, make_files, root):
    make_files(
        root_dir,
        {
            "alpha/report": "exact",
            "alpha/report.txt": "prefix",
            "beta/summary-report.md": "contains",
            "bèta/ÜBERSICHT.TXT": "unicode",
            "beta/100% done.txt": "percent",
            "beta/a_b.txt": "underscore",
            "beta/axb.txt": "no-underscore",
            "archive.tar.gz": "archive",
        },
    )
    scan_root(conn, root)
    return root


def test_exact_prefix_and_contains_ranking(conn, search_root) -> None:
    result = search(conn, "report", SearchFilters(entry_type="file"))
    names = [hit.entry.name for hit in result.hits]
    scores = [hit.score for hit in result.hits]

    assert names[0] == "report"
    assert scores[0] == 100
    assert "report.txt" in names
    assert scores[names.index("report.txt")] == 80
    assert "summary-report.md" in names
    assert scores[names.index("summary-report.md")] == 60
    assert scores == sorted(scores, reverse=True)


def test_search_is_case_insensitive_for_ascii(conn, search_root) -> None:
    lower = search(conn, "report", SearchFilters()).hits
    upper = search(conn, "REPORT", SearchFilters()).hits
    assert [hit.entry.id for hit in lower] == [hit.entry.id for hit in upper]


def test_search_is_case_insensitive_for_unicode(conn, search_root) -> None:
    hits = search(conn, "übersicht", SearchFilters()).hits
    assert len(hits) == 1
    assert hits[0].entry.name == "ÜBERSICHT.TXT"


def test_path_only_match_scores_lower(conn, search_root) -> None:
    hits = search(conn, "alpha", SearchFilters(entry_type="file")).hits
    assert hits
    assert all(hit.score == 30 for hit in hits)
    assert all(hit.reason == "path substring match" for hit in hits)


def test_directory_filter(conn, search_root) -> None:
    hits = search(conn, "alpha", SearchFilters(entry_type="dir")).hits
    assert len(hits) == 1
    assert hits[0].entry.entry_type == "dir"
    assert hits[0].entry.name == "alpha"


def test_extension_filter_is_normalized(conn, search_root) -> None:
    hits = search(conn, "report", SearchFilters(extension=".TXT")).hits
    assert [hit.entry.name for hit in hits] == ["report.txt"]


def test_root_filter(conn, search_root, tmp_path: Path) -> None:
    other_dir = tmp_path / "second root"
    other_dir.mkdir()
    (other_dir / "report-second.txt").write_text("x", encoding="utf-8")
    other_root = add_root(conn, other_dir)
    scan_root(conn, other_root)

    hits = search(conn, "report", SearchFilters(root_id=other_root.id)).hits
    assert [hit.entry.name for hit in hits] == ["report-second.txt"]


def test_limit_is_applied(conn, search_root) -> None:
    result = search(conn, "report", SearchFilters(limit=1))
    assert len(result.hits) == 1
    assert result.limit == 1


def test_like_wildcards_are_escaped(conn, search_root) -> None:
    percent = search(conn, "100%", SearchFilters()).hits
    assert [hit.entry.name for hit in percent] == ["100% done.txt"]

    underscore = search(conn, "a_b", SearchFilters(entry_type="file")).hits
    assert [hit.entry.name for hit in underscore] == ["a_b.txt"]

    only_wildcard = search(conn, "%", SearchFilters(entry_type="file")).hits
    assert {hit.entry.name for hit in only_wildcard} == {"100% done.txt"}


def test_no_results_returns_warning(conn, search_root) -> None:
    result = search(conn, "definitely-not-present-anywhere", SearchFilters())
    assert result.hits == []
    assert any("no matching entries" in warning for warning in result.warnings)


def test_no_roots_warning(tmp_path: Path) -> None:
    with open_database(tmp_path / "empty" / "index.sqlite3") as conn:
        result = search(conn, "anything", SearchFilters())
    assert result.hits == []
    assert any("no indexing roots" in warning for warning in result.warnings)


def test_partial_scan_produces_warning(conn, search_root, root_dir: Path, monkeypatch) -> None:
    real_scandir = os.scandir

    def fake_scandir(path):
        if os.path.basename(os.path.normpath(str(path))) == "alpha":
            raise PermissionError(13, "permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    scan_root(conn, search_root)

    result = search(conn, "report.txt", SearchFilters())
    assert result.hits
    assert any("last scan is partial" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "filters",
    [
        SearchFilters(limit=0),
        SearchFilters(limit=5000),
        SearchFilters(entry_type="symlink"),
        SearchFilters(extension="   "),
    ],
)
def test_invalid_filters_raise(conn, search_root, filters: SearchFilters) -> None:
    with pytest.raises(QueryError):
        search(conn, "report", filters)


def test_empty_query_raises(conn, search_root) -> None:
    with pytest.raises(QueryError):
        search(conn, "   ", SearchFilters(extension=None))


def test_overlong_query_raises(conn, search_root) -> None:
    with pytest.raises(QueryError):
        search(conn, "a" * 501, SearchFilters(extension=None))
