#!/bin/sh
# Build pret's agbcc at a pinned commit into tools/agbcc.
# Needs: git, make, gcc, binutils-arm-none-eabi.
set -e
COMMIT=da598c1d918402c42c0c0d7128ba14567f3175e9
ROOT=$(cd "$(dirname "$0")/.." && pwd)
D=$(mktemp -d)
git clone -q https://github.com/pret/agbcc "$D"
git -C "$D" checkout -q $COMMIT
(cd "$D" && ./build.sh && ./install.sh "$ROOT")
rm -rf "$D"
