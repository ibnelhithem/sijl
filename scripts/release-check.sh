#!/usr/bin/env bash
# The release workflow's checks on your own machine; it stops before anything is published.
#
#   scripts/release-check.sh            build the wheel and sdist, smoke-test both, and
#                                       check them with the publication guard (its private
#                                       word list: the file PUBLICATION_GUARD_LIST names)
#   scripts/release-check.sh v1.2.3     first also check what the tag push will check: the tag
#                                       is v + pyproject's version, and CHANGELOG.md has a
#                                       section for that version (the release notes)
#
# Needs uv and git; downloads build tools and Sijl's dependencies like any install does.
# Builds from the working tree, so commit first: a release is built from the tagged commit.
set -euo pipefail
cd "$(dirname "$0")/.."

release() { uv run --no-project --quiet python scripts/release.py "$@"; }

if [ $# -gt 1 ]; then
    echo "usage: $0 [vX.Y.Z]" >&2
    exit 2
fi
version=$(release version)
echo "release-check: pyproject.toml says $version"
if [ $# -eq 1 ]; then
    release check-tag "$1"
    release notes "$version" >/dev/null
    echo "release-check: tag $1 matches, and CHANGELOG.md has a [$version] section"
else
    echo "release-check: no tag given: the tag and CHANGELOG checks are skipped"
fi
if [ -n "$(git status --porcelain)" ]; then
    echo "release-check: note: uncommitted changes are included in this build" >&2
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/sijl-release.XXXXXX")
# A tag build packages the README whose links point at that tag, then puts the short links
# back. One trap does both: a second trap would replace this cleanup.
cleanup() {
    if [ -n "${readme_copy:-}" ]; then
        cp "$readme_copy" README.md
    fi
    rm -rf "$work"
}
trap cleanup EXIT
if [ $# -eq 1 ]; then
    readme_copy=$work/README.md
    cp README.md "$readme_copy"
    release readme "$1" > README.md
fi
# --no-sources: build exactly as anyone else would, ignoring uv-only [tool.uv.sources] overrides.
uv build --quiet --no-sources --out-dir "$work/dist"
for built in "$work"/dist/sijl-*; do echo "release-check: built $(basename "$built")"; done
uv tool run --from twine==7.0.0 twine check --strict "$work"/dist/*
scripts/smoke-test.sh "$work/dist" "$version"
# The publication guard, on the tree and on exactly the files that would be published. Its word
# list never lives in the repository: PUBLICATION_GUARD_LIST names the maintainer's own copy.
guard_list=${PUBLICATION_GUARD_LIST:-}
if [ -z "$guard_list" ] || [ ! -f "$guard_list" ]; then
    echo "release-check: WARNING: PUBLICATION_GUARD_LIST names no file (${guard_list:-unset}):" >&2
    echo "release-check: WARNING: the private word list was NOT checked on this build." >&2
    echo "release-check: WARNING: a tag push still checks it, and refuses to publish without it." >&2
fi
PUBLICATION_GUARD_DIST="$work/dist" uv run --locked pytest -q -p no:cacheprovider -rs \
    tests/test_publication_guard.py
echo "release-check: Sijl $version is ready to release; nothing was published"
