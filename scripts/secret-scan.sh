#!/bin/sh
# Download the pinned scanner, verify its sha256, and only then scan this repository's history.
# It fetches the Linux x64 build: CI's runner, or a Linux machine by hand.
# A finding names its file, line, rule and commit, never the secret: the report template prints
# nothing else, and --redact masks the secret should a template ever ask for it. Validation stays
# off: it would send a found secret to its provider.
set -eu
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
# 1.9.0 needs the report format named: --report-template alone fails ("Unknown report format").
"$work/betterleaks" git . --no-banner --redact \
    --report-format template --report-template scripts/secret-scan.tmpl --report-path -
