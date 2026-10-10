#!/usr/bin/env bash
cd "${REPO:?set REPO to the seekdb checkout}"
f() { git ls-files "$1" | grep -E "$2" | tr '\n' '\0' | xargs -0 cat 2>/dev/null | wc -l | tr -d ' '; }
c() { git ls-files "$1" | grep -cE "$2"; }
while read -r d p; do
  echo "$d [$p]: files=$(c "$d" "$p") lines=$(f "$d" "$p")"
done <<'LIST'
tools/deploy/mysql_test/t \.test$
tools/deploy/mysql_test/r \.result$
tools/deploy/mysql_test/test_suite \.test$
tools/deploy/mysql_test/test_suite \.result$
tools/deploy/mysql_test/test_suite \.(inc|sql)$
tools/deploy/mysql_test/include \.inc$
tools/deploy/mysql_test .
tools/obtest/t \.test$
tools/obtest/r \.result$
tools/obtest \.inc$
tools/obtest \.sql$
tools/obtest (\.(sh|py)|/obtest|/mytest)$
tools/obtest \.(no|test_no)$
tools/obtest \.jar$
tools/obtest .
rust/sql-nio \.rs$
tools/ob_error/test .
deps/oblib/unittest .
LIST
