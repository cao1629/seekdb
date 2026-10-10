#!/bin/bash
# usage: show.sh <hash> [difflines]
cd "${REPO:?set REPO to the seekdb checkout}" || exit 1
h=$1; n=${2:-60}
echo "=================== $h"
git show -s --format='%h %ad %an%n%s%n%b' --date=short "$h" | sed -e '/<!--/,/-->/d' | grep -v -E '^\s*$|^Co-authored-by|^Co-Authored-By' | head -25
git show --stat --format= "$h" | tail -15
git show --format= -U2 "$h" -- 'src/*' 'deps/oblib/src/*' | grep -v -E '^(index |diff --git)' | head -"$n"
