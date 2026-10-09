#!/bin/bash
set -euo pipefail
# Usage: bash installer/macos/make_icon.sh source.png target.icns
src="$1"
target="$2"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
iconset="$tmp/SRA.iconset"
mkdir -p "$iconset" "$(dirname "$target")"
for n in 16 32 128 256 512; do
  sips -s format png -z "$n" "$n" "$src" --out "$iconset/icon_${n}x${n}.png" >/dev/null
  twice=$((n * 2))
  sips -s format png -z "$twice" "$twice" "$src" --out "$iconset/icon_${n}x${n}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$target"
