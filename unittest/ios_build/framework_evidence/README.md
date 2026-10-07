# Dynamic framework evidence (2026-10-07)

Implementation revision: `6f902fdab24ccefdde97c991b50faf551928b604`. Both unsigned
frameworks were built from a clean source tree. The subsequent evidence/documentation
commit does not change engine or framework implementation. Build manifests preserve
unsigned binary hashes; signing the embedded probe framework changes its signature.

The standalone App only links UIKit. It resolves the desktop API with dlopen/dlsym
and sends SQL through Connector/C over an actual Unix socket. Both platforms passed
two independent process runs, 77 assertions per run, complete/pass/worker_exit=true.
The physical-device persistence counter advanced 3 to 4; simulator advanced 2 to 3.
Processes remained alive after the loading worker exited and were terminated only
after clean engine shutdown was recorded.

```bash
python3 unittest/ios_build/validate_framework_probe.py \
  --revision 6f902fdab24ccefdde97c991b50faf551928b604 \
  unittest/ios_build/framework_evidence/device-run-1.json \
  unittest/ios_build/framework_evidence/device-run-2.json
python3 unittest/ios_build/validate_framework_probe.py \
  --revision 6f902fdab24ccefdde97c991b50faf551928b604 \
  unittest/ios_build/framework_evidence/simulator-run-1.json \
  unittest/ios_build/framework_evidence/simulator-run-2.json
python3 -m unittest discover -s unittest/ios_build -p test_framework_probe_evidence.py
```

`allocator-regression.json` records the pre-fix crash and diagnosed incomplete
bootstrap. SQL success alone did not prove the loading thread could exit. The
six evidence-gate regressions reject missing worker exit, failed cleanup, stale
source identity, unchanged persistent state and duplicate process reports.

Device manifests contain current verified source records for zlib/OpenSSL; the
remaining pre-existing native archives have exact link-inventory hashes, with
pinned recipes tracked in deps/ios-build. The simulator manifest has 14 current
source records. These scopes should not be conflated. Cargo.lock and licenses
are shipped in each framework. No credentials, signing profile, sandbox paths
or raw crash reports are tracked here.

QuickLang integration, App Store acceptance, background operation and active
framework unloading were not tested in this repository task.
