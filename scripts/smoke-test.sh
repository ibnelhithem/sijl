#!/usr/bin/env bash
# Smoke-test built distributions the way a user installs Sijl: the wheel and the sdist
# in DIST are each installed with `uv tool install` into a throwaway tool folder, then the
# installed command must print VERSION and find the right commit in a tiny generated repository.
# With --pypi, the one install is `sijl==VERSION` from pypi.org instead: what PyPI serves now.
#
#   scripts/smoke-test.sh DIST VERSION        e.g.  scripts/smoke-test.sh dist 1.0.0
#   scripts/smoke-test.sh --pypi VERSION      e.g.  scripts/smoke-test.sh --pypi 1.0.0
#
# Used by scripts/release-check.sh and the release workflow (.github/workflows/release.yml).
# Written for bash 3.2 and BSD tools as well (macOS); writes only inside one temporary folder.
set -euo pipefail

if [ $# -ne 2 ]; then
    echo "usage: $0 DIST VERSION | $0 --pypi VERSION" >&2
    exit 2
fi
version=$2
if [ "$1" = --pypi ]; then
    kinds=pypi
    # Only pypi.org: no index, file folder or config file named in the environment is used.
    unset UV_INDEX UV_INDEX_URL UV_EXTRA_INDEX_URL UV_FIND_LINKS UV_CONFIG_FILE
else
    kinds="wheel sdist"
    dist=$1
fi
python=${SIJL_SMOKE_PYTHON:-3.12} # the oldest Python Sijl supports
email=smoke@example.invalid

fail() {
    echo "smoke-test: FAILED: $1" >&2
    exit 1
}

if [ "$kinds" != pypi ]; then
    [ -d "$dist" ] || fail "no folder $dist"
    dist=$(cd "$dist" && pwd) # absolute: the installs below run from inside a temporary folder
    wheel="$dist/sijl-$version-py3-none-any.whl"
    sdist="$dist/sijl-$version.tar.gz"
    [ -f "$wheel" ] || fail "no wheel for $version in $dist (expected $(basename "$wheel"))"
    [ -f "$sdist" ] || fail "no sdist for $version in $dist (expected $(basename "$sdist"))"
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/sijl-smoke.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir "$work/home"

# The sample repository: one commit inside January 2026 and one just outside it. git runs with
# no user or system config, so signing, hooks or templates on this machine cannot interfere.
repo="$work/sample/app"
mkdir -p "$repo"
sample_git() {
    env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 git -C "$repo" "$@"
}
commit() { # commit DATE SUBJECT: an empty commit by the sample author on that date
    env GIT_AUTHOR_NAME="Smoke Test" GIT_AUTHOR_EMAIL="$email" GIT_AUTHOR_DATE="$1" \
        GIT_COMMITTER_NAME="Smoke Test" GIT_COMMITTER_EMAIL="$email" GIT_COMMITTER_DATE="$1" \
        GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
        git -C "$repo" commit --quiet --allow-empty -m "$2"
}
sample_git init --quiet -b main
commit "2025-12-31 12:00:00 +0000" "chore: outside the period"
commit "2026-01-15 12:00:00 +0000" "feat: smoke-test commit"
sha=$(sample_git rev-parse --short=7 HEAD)

cd "$work" # nothing from the source tree can be imported by accident
for kind in $kinds; do
    case $kind in
        wheel) set -- "$wheel" ;;
        sdist) set -- "$sdist" ;;
        # No cache and no uv config file, and pypi.org as the only index: nothing on this
        # machine can stand in for the files PyPI serves.
        pypi)
            set -- --no-cache --no-config --default-index https://pypi.org/simple "sijl==$version"
            ;;
    esac
    bin="$work/bin-$kind"

    # Its own tool and bin folders: never the user's installed Sijl, never their PATH.
    UV_TOOL_DIR="$work/tools-$kind" UV_TOOL_BIN_DIR="$bin" \
        uv tool install --quiet --python "$python" "$@" 2>"$work/install.log" ||
        { cat "$work/install.log" >&2; fail "$kind: uv tool install failed"; }

    printed=$("$bin/sijl" --version)
    [ "$printed" = "sijl $version" ] ||
        fail "$kind: --version printed '$printed', expected 'sijl $version'"

    # A clean environment: a throwaway HOME, so no config file or global git config of this
    # machine is read, and no SIJL_* variable is inherited.
    report=$(env -i PATH="$PATH" HOME="$work/home" TZ=UTC "$bin/sijl" report \
        --root "$work/sample" --author-email "$email" --since 2026-01-01 --until 2026-01-31) ||
        fail "$kind: sijl report exited with $?"
    case "$report" in
        *"feat: smoke-test commit"*) ;;
        *) fail "$kind: the report does not list the commit in the period" ;;
    esac
    case "$report" in
        *"$sha"*) ;;
        *) fail "$kind: the report does not show the commit's SHA $sha" ;;
    esac
    case "$report" in
        *"chore: outside the period"*) fail "$kind: the report lists a commit outside the period" ;;
    esac
    echo "smoke-test: $kind OK — $printed; the report lists the one commit in its period"
done
