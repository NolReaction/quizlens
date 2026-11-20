#!/bin/bash
# GitHub CLI authenticates via GH_TOKEN, restricted to this one publishing job.
set -euo pipefail
tag=${GITHUB_REF_NAME#release/}
repo=$GITHUB_REPOSITORY
commit=$(git rev-parse HEAD)
assets=(QuizLens-Mac.zip QuizLens-Windows.zip QuizLens-Extension.zip QuizLens-Guide.md release.json SHA256SUMS.txt)
paths=()
for asset in "${assets[@]}"; do paths+=("dist/$asset"); done
status=$(mktemp)
trap 'rm -f "$status"' EXIT
tag_ref="refs/tags/$tag"
api="repos/$repo/git"

# Read the exact tag ref, including when another branch has the same short name.
# Follow annotated tags to the commit used for the builds.
check_tag() {
  local object type sha
  object=$(gh api "$api/ref/tags/$tag" --jq '.object | [.type, .sha] | @tsv')
  read -r type sha <<< "$object"
  while [[ "$type" == tag ]]; do
    object=$(gh api "$api/tags/$sha" --jq '.object | [.type, .sha] | @tsv')
    read -r type sha <<< "$object"
  done
  if [[ "$type" != commit || "$sha" != "$commit" ]]; then
    echo "Tag $tag identifies $type $sha; this release was built from $commit. Refusing to overwrite the tag." >&2
    exit 1
  fi
}

if [[ "$GITHUB_REF_TYPE" == branch ]]; then
  # A failed create can also mean a concurrent run has already created the ref.
  # The required read-back below distinguishes an identical commit from a conflict.
  existing=$(gh api "$api/matching-refs/tags/$tag" | jq -r --arg ref "$tag_ref" '.[] | select(.ref == $ref) | .ref')
  if [[ -z "$existing" ]]; then
    gh api --method POST "$api/refs" -f ref="$tag_ref" -f sha="$commit" --silent || echo "Verifying tag after an unsuccessful create." >&2
  fi
fi
check_tag
if gh release view "$tag" --repo "$repo" --json isDraft,body,assets > "$status"; then
  if ! jq -e --arg owner "<!-- quizlens-release: $commit -->" '.body | contains($owner)' "$status" >/dev/null; then
    echo "A release already exists for this tag and was not created by this workflow for this commit." >&2
    exit 1
  fi
  if jq -e '.isDraft == false' "$status" >/dev/null; then
    # A retried successful publish keeps the original archives and signatures.
    for asset in "${assets[@]}"; do
      jq -e --arg n "$asset" '.assets | any(.name == $n and .size > 0)' "$status" >/dev/null
    done
    echo "Release $tag is already published with all assets."
    exit 0
  fi
else
  gh release create "$tag" --repo "$repo" --verify-tag --draft --title "QuizLens $tag" --notes-file dist/release-notes.md
fi
# A retried failed transfer can only replace assets on the draft owned by this run/commit.
gh release upload "$tag" "${paths[@]}" --repo "$repo" --clobber
gh release view "$tag" --repo "$repo" --json isDraft,assets > "$status"
jq -e '.isDraft == true' "$status" >/dev/null
for asset in "${assets[@]}"; do
  size=$(wc -c < "dist/$asset")
  jq -e --arg name "$asset" --argjson size "$size" '.assets | any(.name == $name and .size == $size)' "$status" >/dev/null
done
check_tag
gh release edit "$tag" --repo "$repo" --notes-file dist/release-notes.md --draft=false
