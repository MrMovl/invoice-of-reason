"""scripts/deploy-check.sh: only committed versions that are on origin/main may be deployed."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deploy-check.sh"
pytestmark = pytest.mark.skipif(not SCRIPT.exists() or not shutil.which("git"),
                                reason="needs git and the scripts directory (not in the Docker test image)")


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"})


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", str(origin), str(work))
    (work / "app.txt").write_text("v1\n")
    git(work, "add", "app.txt")
    git(work, "commit", "-m", "v1")
    git(work, "push", "origin", "HEAD:main")
    return work


def check(work, **env):
    return subprocess.run([str(SCRIPT)], cwd=work, capture_output=True, text=True,
                          env={**os.environ, "ALLOW_DIRTY": "", "ALLOW_UNMERGED": "", **env})


def test_commit_on_main_is_deployable(repo):
    result = check(repo)
    assert result.returncode == 0, result.stderr
    assert not result.stdout.strip().split()[0].endswith(("-dirty", "-unmerged"))


def test_uncommitted_changes_are_refused_unless_marked(repo):
    (repo / "app.txt").write_text("changed\n")
    assert check(repo).returncode == 1
    result = check(repo, ALLOW_DIRTY="1")
    assert result.returncode == 0 and "-dirty" in result.stdout


def test_branch_commit_not_on_main_is_refused_unless_marked(repo):
    git(repo, "checkout", "-b", "feature")
    (repo / "app.txt").write_text("v2\n")
    git(repo, "commit", "-am", "v2")
    git(repo, "push", "origin", "feature")
    refused = check(repo)
    assert refused.returncode == 1 and "not on origin/main" in refused.stderr
    marked = check(repo, ALLOW_UNMERGED="1")
    assert marked.returncode == 0 and "-unmerged" in marked.stdout

    git(repo, "checkout", "main")
    git(repo, "merge", "--no-ff", "feature", "-m", "Merge feature")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "feature")
    assert check(repo).returncode == 0  # the merged commit is now on main
