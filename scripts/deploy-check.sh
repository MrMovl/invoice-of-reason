#!/usr/bin/env bash
# Refuse to deploy a program version that git history cannot prove later (GoBD Rz. 154,
# Programmidentität). Prints the version string for the image on success.
#
# - No uncommitted changes (ALLOW_DIRTY=1 builds a version marked "-dirty").
# - HEAD must be contained in origin/main after fetching. A commit that only exists on a PR
#   branch can vanish (squash, rebase, deleted branch) and the deployed version would no longer
#   be traceable. main is protected against force pushes and deletion, and PRs are merged with
#   merge commits only (GitHub settings), so commits on main stay. ALLOW_UNMERGED=1 builds a
#   version marked "-unmerged" for emergencies.
set -euo pipefail

REMOTE="${DEPLOY_REMOTE:-origin}"
BRANCH="${DEPLOY_BRANCH:-main}"
suffix=""

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  if [ "${ALLOW_DIRTY:-}" != "1" ]; then
    echo "Uncommitted changes. Commit first (or ALLOW_DIRTY=1 for a marked -dirty build)." >&2
    exit 1
  fi
fi

git fetch --quiet "$REMOTE" "$BRANCH"
if ! git merge-base --is-ancestor HEAD "$REMOTE/$BRANCH"; then
  if [ "${ALLOW_UNMERGED:-}" != "1" ]; then
    echo "HEAD $(git rev-parse --short HEAD) is not on $REMOTE/$BRANCH. Merge the PR (merge commit)," >&2
    echo "pull $BRANCH and deploy from there (or ALLOW_UNMERGED=1 for a marked -unmerged build)." >&2
    exit 1
  fi
  suffix="-unmerged"
fi

echo "$(git describe --tags --always --dirty=-dirty)$suffix $(git log -1 --format=%cs)"
