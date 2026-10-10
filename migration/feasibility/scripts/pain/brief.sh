#!/bin/bash
cd "${REPO:?set REPO to the seekdb checkout}" || exit 1
for h in "$@"; do
echo "=================== $h $(git show -s --format='%s' $h)"
git show -s --format='%b' "$h" | sed -e '/<!--/,/-->/d' | grep -v -E '^\s*$|^Co-|^#|^\* ' | head -4
git show --stat --format= "$h" | tail -1
git show --format= -U1 "$h" -- 'src/*' 'deps/oblib/src/*' | grep -E '^[-+]' | grep -v -E '^(\+\+\+|---) ' | head -${N:-18}
done
