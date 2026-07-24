#!/bin/sh
# Fail if the declared versions disagree with each other or with a release tag.
#
# The project version is read at runtime by config.server_version() and
# reported as serverInfo.version, the manifest version is what Claude Desktop
# uses for upgrade detection, and the tag is what the release artifacts are
# published under. Nothing derives any of these from the others, so they are
# checked here instead: v1.1.2 and v1.1.3 both shipped a server reporting
# 1.1.0.
#
# Usage:
#   check-versions.sh            # the declared versions must agree
#   check-versions.sh v1.3.0     # ...and must equal this release tag
#
# Deliberately POSIX sh plus jq, both present on CI runners without setup, so
# the guard does not depend on which Python or Node a workflow happens to have.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MANIFEST="desktop-extension/manifest.json"
PACKAGE="desktop-extension/package.json"

# Read version from the [project] table only, so other tables cannot match.
pyproject_version=$(
    sed -n '/^\[project\]/,/^\[[^p]/ s/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$ROOT/pyproject.toml" | head -n 1
)
manifest_version=$(jq -r '.version' "$ROOT/$MANIFEST")
package_version=$(jq -r '.version' "$ROOT/$PACKAGE")

for pair in "pyproject.toml:$pyproject_version" "$MANIFEST:$manifest_version" \
    "$PACKAGE:$package_version"; do
    value=${pair#*:}
    if [ -z "$value" ] || [ "$value" = "null" ]; then
        echo "ERROR: could not read a version from ${pair%%:*}" >&2
        exit 1
    fi
done

tag=${1:-}
expected=""
if [ -n "$tag" ]; then
    # Tags are written v1.3.0; the declared versions are not prefixed.
    expected=${tag#v}
fi

printf '  %-12s %s\n' "$pyproject_version" "pyproject.toml"
printf '  %-12s %s\n' "$manifest_version" "$MANIFEST"
printf '  %-12s %s\n' "$package_version" "$PACKAGE"
[ -n "$expected" ] && printf '  %-12s %s\n' "$expected" "release tag $tag"

mismatch=0
[ "$manifest_version" = "$pyproject_version" ] || mismatch=1
[ "$package_version" = "$pyproject_version" ] || mismatch=1
if [ -n "$expected" ] && [ "$expected" != "$pyproject_version" ]; then
    mismatch=1
fi

if [ "$mismatch" -ne 0 ]; then
    if [ -n "$expected" ]; then
        target="the release tag"
    else
        target="each other"
    fi
    cat >&2 <<EOF

ERROR: version mismatch, every version above must match $target.
Update pyproject.toml (run 'uv lock' afterwards so uv.lock follows),
$MANIFEST and $PACKAGE.
EOF
    exit 1
fi

echo
echo "All versions agree: $pyproject_version"
