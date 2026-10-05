#!/bin/sh
# Main-only publisher consuming the immutable verified quality artifact.
set -eu
: "${GH_TOKEN:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SHA:?}"
[ "${GITHUB_EVENT_NAME:-}" = push ] && [ "${GITHUB_REF:-}" = refs/heads/main ] || { echo 'Only a main push may publish' >&2; exit 1; }
python3 -c 'import hashlib,json,os,re; from pathlib import Path; p=json.loads(Path("artifacts/package-verification.json").read_text()); wheel=Path("artifacts")/p["wheel"]; valid=p["source_sha"]==os.environ["GITHUB_SHA"] and p["reproducible"] is True and p["clean_install"] is True and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",p["version"]) and wheel.parent==Path("artifacts") and wheel.suffix==".whl" and hashlib.sha256(wheel.read_bytes()).hexdigest()==p["sha256"] and Path("artifacts/SHA256SUMS").read_text()==p["sha256"]+"  "+p["wheel"]+"\n"; valid or (_ for _ in ()).throw(ValueError("unverified release input"))'
VERSION=$(python3 -c 'import json; from pathlib import Path; print(json.loads(Path("artifacts/package-verification.json").read_text())["version"])')
TAG="v$VERSION"
if gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG" > artifacts/release.json 2> artifacts/release-error.txt; then
  DRAFT=$(python3 -c 'import json; from pathlib import Path; print(str(json.loads(Path("artifacts/release.json").read_text())["draft"]).lower())')
else
  if ! grep -q '(HTTP 404)' artifacts/release-error.txt; then
    cat artifacts/release-error.txt >&2
    exit 1
  fi
  if gh api "repos/$GITHUB_REPOSITORY/commits/$TAG" --jq .sha > artifacts/tag-sha.txt 2> artifacts/tag-error.txt; then
    [ "$(cat artifacts/tag-sha.txt)" = "$GITHUB_SHA" ] || { echo 'Version tag belongs to another source' >&2; exit 1; }
  elif grep -q '(HTTP 404)' artifacts/tag-error.txt; then
    gh api --method POST "repos/$GITHUB_REPOSITORY/git/refs" -f ref="refs/tags/$TAG" -f sha="$GITHUB_SHA" --silent
  else
    cat artifacts/tag-error.txt >&2
    exit 1
  fi
  gh api "repos/$GITHUB_REPOSITORY/releases/generate-notes" -f tag_name="$TAG" -f target_commitish="$GITHUB_SHA" --jq .body > artifacts/CHANGELOG.md
  gh release create "$TAG" --draft --repo "$GITHUB_REPOSITORY" --target "$GITHUB_SHA" --title "$TAG" --notes-file artifacts/CHANGELOG.md
  gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG" > artifacts/release.json
  DRAFT=true
fi
if [ "$DRAFT" = true ]; then
  # An interrupted upload is recoverable, but only for the same source commit.
  python3 -c 'import json,os; from pathlib import Path; p=json.loads(Path("artifacts/release.json").read_text()); p["target_commitish"]==os.environ["GITHUB_SHA"] or (_ for _ in ()).throw(ValueError("draft belongs to another source; do not replace it")); Path("artifacts/CHANGELOG.md").write_text(p["body"]+"\n")'
  TAG_SHA=$(gh api "repos/$GITHUB_REPOSITORY/commits/$TAG" --jq .sha)
  [ "$TAG_SHA" = "$GITHUB_SHA" ] || { echo 'Version tag changed' >&2; exit 1; }
  # Measurement provenance outlives the 14-day CI artifact.
  gh release upload "$TAG" artifacts/*.whl artifacts/SHA256SUMS artifacts/package-verification.json artifacts/CHANGELOG.md artifacts/coverage.json artifacts/dependency-audit.json artifacts/wheelhouse.json --repo "$GITHUB_REPOSITORY" --clobber
fi
mkdir -p artifacts/existing
gh release download "$TAG" --repo "$GITHUB_REPOSITORY" --pattern SHA256SUMS --pattern '*.whl' --pattern package-verification.json --pattern CHANGELOG.md --dir artifacts/existing --clobber
cmp artifacts/SHA256SUMS artifacts/existing/SHA256SUMS
python3 -c 'import hashlib,json; from pathlib import Path; p=json.loads(Path("artifacts/package-verification.json").read_text()); existing=Path("artifacts/existing"); actual=hashlib.sha256((existing/p["wheel"]).read_bytes()).hexdigest(); released=json.loads((existing/"package-verification.json").read_text()); valid=actual==p["sha256"] and released["sha256"]==p["sha256"] and released["version"]==p["version"] and released["reproducible"] is True and released["clean_install"] is True and (existing/"CHANGELOG.md").is_file(); valid or (_ for _ in ()).throw(ValueError("existing release differs or is incomplete; bump version"))'
if [ "$DRAFT" = true ]; then
  gh release edit "$TAG" --repo "$GITHUB_REPOSITORY" --draft=false
fi
# A successful old run can still be rerun manually. Never replace newer
# measurements with its results, even though it could fast-forward badges.
CURRENT_MAIN=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq .object.sha)
if [ "$CURRENT_MAIN" != "$GITHUB_SHA" ]; then
  echo 'Skipping badge publication: source is no longer main'
  exit 0
fi
# One commit publishes all measurements atomically. A concurrent update fails
# the non-force ref update rather than overwriting a newer measurement.
BASE=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/badges" --jq .object.sha)
TREE=$(gh api "repos/$GITHUB_REPOSITORY/git/commits/$BASE" --jq .tree.sha)
BADGE_BASE_TREE="$TREE" python3 -c 'import json,os; from pathlib import Path; files=[]; p=json.loads(Path("artifacts/package-verification.json").read_text()); [(files.append({"path":name+".json","mode":"100644","type":"blob","content":Path("artifacts/badge-"+name+".json").read_text()})) for name in ("coverage","license","python") if json.loads(Path("artifacts/badge-"+name+".json").read_text())["sourceSha"]==p["source_sha"]]; len(files)==3 or (_ for _ in ()).throw(ValueError("badge provenance mismatch")); Path("artifacts/badge-tree.json").write_text(json.dumps({"base_tree":os.environ["BADGE_BASE_TREE"],"tree":files}))'
NEW_TREE=$(gh api --method POST "repos/$GITHUB_REPOSITORY/git/trees" --input artifacts/badge-tree.json --jq .sha)
COMMIT=$(gh api --method POST "repos/$GITHUB_REPOSITORY/git/commits" -f message="Measured badges from $GITHUB_SHA" -f tree="$NEW_TREE" -f "parents[]=$BASE" --jq .sha)
gh api --method PATCH "repos/$GITHUB_REPOSITORY/git/refs/heads/badges" -f sha="$COMMIT" -F force=false --silent
