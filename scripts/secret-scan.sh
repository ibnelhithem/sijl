#!/bin/sh
# Download the pinned scanner, verify its sha256, and only then scan:
#
#   sh scripts/secret-scan.sh            this repository's git history (the ci.yml job)
#   sh scripts/secret-scan.sh dist DIR   every wheel and sdist in DIR, unpacked (the release)
#
# It fetches the Linux x64 build: CI's runner, or a Linux machine by hand.
# A finding names its file, line, rule and commit, never the secret: the report template prints
# nothing else, and --redact masks the secret should a template ever ask for it. Validation stays
# off: it would send a found secret to its provider.
set -eu

if [ $# -eq 2 ] && [ "$1" = dist ] && [ -d "$2" ]; then
    built=$(cd "$2" && pwd) # absolute: the scan runs from the repository's top folder
elif [ $# -ne 0 ]; then
    echo "usage: $0 [dist DIR]" >&2
    exit 2
fi
cd "$(dirname "$0")/.."

work=$(mktemp -d "${TMPDIR:-/tmp}/sijl-secret-scan.XXXXXX")
trap 'rm -rf "$work"' EXIT

version=1.9.0
archive=betterleaks_${version}_linux_x64.tar.gz
sum=f8b185a39ffcece2a1ca82bf3a4e7435cd81963ffd16b7a9128daf75f35f6de7
url="https://github.com/betterleaks/betterleaks/releases/download/v${version}/${archive}"

curl -fsSL --retry 3 --max-time 120 -o "$work/$archive" "$url"
printf '%s  %s\n' "$sum" "$archive" > "$work/checksums.txt"
(cd "$work" && sha256sum -c checksums.txt)

tar -xzf "$work/$archive" -C "$work"

if [ -n "${built:-}" ]; then
    # Each archive into a folder of its own name, so a finding says which file it came from.
    mkdir "$work/unpacked"
    for file in "$built"/*.whl "$built"/*.tar.gz; do
        [ -f "$file" ] || continue
        into="$work/unpacked/$(basename "$file")"
        mkdir "$into"
        case $file in
            *.whl) python3 -m zipfile -e "$file" "$into" ;;
            *) tar -xzf "$file" -C "$into" ;;
        esac
    done
    if [ -z "$(ls "$work/unpacked")" ]; then
        echo "secret-scan: no wheel or sdist in $built" >&2
        exit 1
    fi
    set -- dir "$work/unpacked"
else
    set -- git .
fi
# The allowlist is named: a folder scan would look for it inside the unpacked files.
# 1.9.0 needs the report format named: --report-template alone fails ("Unknown report format").
"$work/betterleaks" "$@" --config .betterleaks.toml --no-banner --redact \
    --report-format template --report-template scripts/secret-scan.tmpl --report-path -
