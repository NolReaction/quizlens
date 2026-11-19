#!/bin/bash
# GitHub CLI authenticates via GH_TOKEN, restricted to this one publishing job.
set -euo pipefail
tag=$GITHUB_REF_NAME
repo=$GITHUB_REPOSITORY
commit=$(git rev-parse HEAD)
assets=(QuizLens-Mac.zip QuizLens-Windows.zip QuizLens-Guide.md release.json SHA256SUMS.txt)
paths=()
for asset in "${assets[@]}"; do paths+=("dist/$asset"); done
status=$(mktemp)
trap 'rm -f "$status"' EXIT
check_tag() {
  local remote
  remote=$(gh api "repos/$repo/commits/$tag" --jq '.sha')
  if [[ "$remote" != "$commit" ]]; then
    echo "Tag $tag no longer identifies the commit used for testing and packaging." >&2
    exit 1
  fi
}
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
