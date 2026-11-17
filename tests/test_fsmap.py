from __future__ import annotations

from pathlib import Path

import pytest

from taskagent.fsmap import FolderBrowser, PathBoundaryError


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "plain").mkdir(parents=True)
    repo_git = root / "myrepo" / ".git"
    repo_git.mkdir(parents=True)
    (repo_git / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/acme/myrepo.git\n',
        encoding="utf-8",
    )
    (root / "myrepo" / "src").mkdir()
    (root / ".hidden").mkdir()
    return root


def test_browse_lists_real_paths_and_hides_dotfiles(tree: Path):
    browser = FolderBrowser(tree)
    result = browser.browse("")
    assert result["current"] == str(tree.resolve())
    assert result["at_root"] is True
    names = {d["name"] for d in result["dirs"]}
    assert "plain" in names and "myrepo" in names
    assert ".hidden" not in names
    myrepo = next(d for d in result["dirs"] if d["name"] == "myrepo")
    assert myrepo["path"] == str((tree / "myrepo").resolve())
    assert myrepo["is_git"] is True


def test_browse_child_and_parent(tree: Path):
    browser = FolderBrowser(tree)
    result = browser.browse(str(tree / "myrepo"))
    assert result["current"] == str((tree / "myrepo").resolve())
    assert result["parent"] == str(tree.resolve())
    assert result["at_root"] is False
    assert {d["name"] for d in result["dirs"]} == {"src"}


def test_browse_rejects_escape(tree: Path):
    browser = FolderBrowser(tree)
    with pytest.raises(PathBoundaryError):
        browser.browse(str(tree.parent))


def test_scan_finds_git_first_and_suggests_name(tree: Path):
    browser = FolderBrowser(tree)
    result = browser.scan("", depth=2)
    cands = result["candidates"]
    assert cands[0]["is_git"] is True
    assert cands[0]["candidate_name"] == "myrepo"
    assert cands[0]["origin"] == "https://github.com/acme/myrepo.git"
    paths = {c["path"] for c in cands}
    assert str((tree / "plain").resolve()) in paths


def test_scan_depth_zero_only_picked_folder(tree: Path):
    browser = FolderBrowser(tree)
    result = browser.scan(str(tree / "myrepo"), depth=0)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["path"] == str((tree / "myrepo").resolve())
