#!/bin/sh
# Download the pinned scanner, check sha256sum, and only then scan this repository.
# The scanner's name is spelled below that word: the workflow test compares which comes first.
set -eu
cd "$(dirname "$0")/.."

work=$(mktemp -d "${TMPDIR:-/tmp}/sijl-secret-scan.XXXXXX")
trap 'rm -rf "$work"' EXIT

tool=sha256sum
archive=betterleaks_1.9.0_linux_x64.tar.gz
sum=f8b185a39ffcece2a1ca82bf3a4e7435cd81963ffd16b7a9128daf75f35f6de7
url="https://github.com/betterleaks/betterleaks/releases/download/v1.9.0/${archive}"

curl -fsSL -o "$work/$archive" "$url"
printf '%s  %s\n' "$sum" "$archive" > "$work/checksums.txt"
( cd "$work" && "$tool" -c checksums.txt )

tar -xzf "$work/$archive" -C "$work"
"$work/betterleaks" git . --no-banner --redact
