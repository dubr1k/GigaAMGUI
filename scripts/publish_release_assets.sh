#!/usr/bin/env bash
# Publish a GitHub release from a directory of built assets, one upload at a time.
#
#   scripts/publish_release_assets.sh <tag> <assets-dir> <notes-file>
#
# softprops/action-gh-release uploads all assets in parallel; on 2026-09-17 the
# API answered "Error saving asset" three runs in a row for the 11 × ~1 GB
# GigaAM archives, and the release stayed a draft with no files. Uploading
# sequentially with retries is slower but survives that. The release is created
# as a draft (or reused if the tag already has one), and only flipped to
# published once every asset is attached, so a half-uploaded release is never
# visible as "Latest".
#
# Needs: gh authenticated with contents:write (GITHUB_TOKEN / GH_TOKEN), and
# GH_REPO or a checkout of the repository.
set -euo pipefail

TAG="${1:?tag, e.g. v2.1.3}"
ASSETS_DIR="${2:?directory with release assets}"
NOTES="${3:?release notes file}"
VERSION="${TAG#v}"
ATTEMPTS="${PUBLISH_UPLOAD_ATTEMPTS:-5}"

shopt -s nullglob
FILES=("$ASSETS_DIR"/*)
if [ "${#FILES[@]}" -eq 0 ]; then
  echo "No assets found in $ASSETS_DIR" >&2
  exit 1
fi
test -f "$NOTES" || { echo "Missing release notes: $NOTES" >&2; exit 1; }

if gh release view "$TAG" >/dev/null 2>&1; then
  echo "Release $TAG exists; refreshing title and notes"
  gh release edit "$TAG" --draft --title "$VERSION" --notes-file "$NOTES" >/dev/null
else
  echo "Creating draft release $TAG"
  gh release create "$TAG" --draft --title "$VERSION" --notes-file "$NOTES" >/dev/null
fi

for file in "${FILES[@]}"; do
  name=$(basename "$file")
  for attempt in $(seq 1 "$ATTEMPTS"); do
    echo "Uploading $name (attempt $attempt/$ATTEMPTS)"
    if gh release upload "$TAG" "$file" --clobber; then
      break
    fi
    if [ "$attempt" -eq "$ATTEMPTS" ]; then
      echo "Giving up on $name" >&2
      exit 1
    fi
    sleep $((attempt * 20))
  done
done

UPLOADED=$(gh release view "$TAG" --json assets --jq '.assets | length')
if [ "$UPLOADED" -ne "${#FILES[@]}" ]; then
  echo "Expected ${#FILES[@]} assets on $TAG, found $UPLOADED" >&2
  exit 1
fi

gh release edit "$TAG" --draft=false --latest >/dev/null
echo "Published $TAG with $UPLOADED assets"
