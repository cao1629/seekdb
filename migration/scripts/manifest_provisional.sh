#!/bin/sh
# Kit Step 1 closing action (prompts/01): migration/manifest.tsv from the header-group
# order, one row per header group. PROVISIONAL naming until the design doc fixes its
# naming rules: src/<module>/<path>/<stem>.<ext> -> rust/port/<module>/src/<path>/<stem>.rs.
# Module prefixes go through @@<module>@@ markers first so that no later substitution
# can match inside an already rewritten path. Run from the repository root.
set -e
python3 -I migration/scripts/make_manifest.py --order migration/depmap/units/representatives.txt --out migration/manifest.tsv \
  --sub 'src/data_plane/=@@data_plane@@/' --sub 'src/logservice/=@@logservice@@/' --sub 'src/objit/=@@objit@@/' \
  --sub 'src/oblib/=@@oblib@@/' --sub 'src/observer/=@@observer@@/' --sub 'src/pl/=@@pl@@/' --sub 'src/query/=@@query@@/' \
  --sub 'src/rootserver/=@@rootserver@@/' --sub 'src/share/=@@share@@/' --sub 'src/sql/=@@sql@@/' \
  --sub 'src/standby/=@@standby@@/' --sub 'src/storage/=@@storage@@/' \
  --sub '.cpp=.rs' --sub '.cc=.rs' --sub '.hpp=.rs' --sub '.ipp=.rs' --sub '.inc=.rs' --sub '.def=.rs' --sub '.map=.rs' \
  --sub '.h=.rs' --sub '.c=.rs' \
  --sub '@@data_plane@@/=rust/port/data_plane/src/' --sub '@@logservice@@/=rust/port/logservice/src/' \
  --sub '@@objit@@/=rust/port/objit/src/' --sub '@@oblib@@/=rust/port/oblib/src/' \
  --sub '@@observer@@/=rust/port/observer/src/' --sub '@@pl@@/=rust/port/pl/src/' --sub '@@query@@/=rust/port/query/src/' \
  --sub '@@rootserver@@/=rust/port/rootserver/src/' --sub '@@share@@/=rust/port/share/src/' \
  --sub '@@sql@@/=rust/port/sql/src/' --sub '@@standby@@/=rust/port/standby/src/' --sub '@@storage@@/=rust/port/storage/src/'
wc -l migration/manifest.tsv
