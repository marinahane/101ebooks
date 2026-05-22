#!/usr/bin/env bash
set -euo pipefail

TAG=v1

gh release create "$TAG" --title "Book files" --notes "" 2>/dev/null || true

echo "Uploading EPUBs..."
for f in epub/universal/*.epub; do
    echo "  $(basename "$f")"
    gh release upload "$TAG" "$f" --clobber
done

echo "Uploading XTCH (X3)..."
for f in xtch/x3/*.xtch; do
    base=$(basename "$f" .xtch)
    echo "  ${base}-x3.xtch"
    gh release upload "$TAG" "$f#${base}-x3.xtch" --clobber
done

echo "Uploading XTCH (X4)..."
for f in xtch/x4/*.xtch; do
    base=$(basename "$f" .xtch)
    echo "  ${base}-x4.xtch"
    gh release upload "$TAG" "$f#${base}-x4.xtch" --clobber
done

echo "Done."
