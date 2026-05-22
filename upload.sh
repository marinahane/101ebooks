#!/usr/bin/env bash
set -euo pipefail

TAG=v1
START_FROM="${1:-}"
skip=false
[[ -n "$START_FROM" ]] && skip=true

TMPDIR_UPLOAD=$(mktemp -d)
trap 'rm -rf "$TMPDIR_UPLOAD"' EXIT

upload() {
    local display="$1" src="$2"
    if $skip; then
        if [[ "$display" == "$START_FROM" ]]; then
            skip=false
        else
            echo "  (skipping) $display"
            return
        fi
    fi
    echo "  $display"
    local dest="$TMPDIR_UPLOAD/$display"
    cp "$src" "$dest"
    gh release upload "$TAG" "$dest" --clobber
}

gh release create "$TAG" --title "Book files" --notes "" 2>/dev/null || true

echo "Uploading EPUBs..."
for f in epub/universal/*.epub; do
    base=$(basename "$f")
    upload "$base" "$f"
done

echo "Uploading XTCH (X3)..."
for f in xtch/x3/*.xtch; do
    base=$(basename "$f" .xtch)
    upload "${base}-x3.xtch" "$f"
done

echo "Uploading XTCH (X4)..."
for f in xtch/x4/*.xtch; do
    base=$(basename "$f" .xtch)
    upload "${base}-x4.xtch" "$f"
done

echo "Done."
