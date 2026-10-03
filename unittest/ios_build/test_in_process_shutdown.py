#!/usr/bin/env python3
"""Native regression for canceling package DDL before shutdown joins its loader."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class InProcessShutdownTest(unittest.TestCase):
    """Exercise the production cancellation helper against a lock-holding loader."""

    def test_background_ddl_releases_its_query_lock_before_join(self):
        """Compile the actual helper and reject a deadlock caused by reversing its calls."""
        source = (ROOT / 'src/observer/ob_server.cpp').read_text()
        start = source.index('static void stop_in_process_package_ddl(')
        end = source.index('\nint ObServer::stop()', start)
        helper = source[start:end]
        harness = r'''
#include <atomic>
#include <cassert>
#include <chrono>
#include <mutex>
#include <thread>
namespace rootserver {
/** Model the existing cancellation flag inspected by schema_retry_to_die. */
class ObDDLServiceLauncher {
public:
  std::atomic<bool> active{true};
  /** Publish cancellation before any join can wait on a DDL query. */
  void deactivate() { active.store(false); }
};
/** Model the loader holding its query lock until DDL cancellation is visible. */
class ObSystemPackageLoadService {
public:
  std::mutex query;
  std::atomic<bool> running{false};
  bool waited = false;
  std::thread worker;
  /** Start a deterministic background query and wait until it owns its lock. */
  explicit ObSystemPackageLoadService(ObDDLServiceLauncher &launcher)
    : worker([this, &launcher] {
      std::lock_guard<std::mutex> lock(query);
      running.store(true);
      while (launcher.active.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }) {
    while (!running.load()) std::this_thread::yield();
  }
  /** Join the active task, matching the real loader stop/wait_task contract. */
  void stop() { worker.join(); }
  /** Record the subsequent timer-drain phase. */
  int wait() { waited = true; return 0; }
};
}
'''
        harness += helper + r'''
/** Require cancellation, task completion, and an available SQL query lock. */
int main() {
  rootserver::ObDDLServiceLauncher launcher;
  rootserver::ObSystemPackageLoadService loader(launcher);
  stop_in_process_package_ddl(&launcher, &loader);
  assert(!launcher.active.load() && loader.waited);
  assert(loader.query.try_lock());
  loader.query.unlock();
  stop_in_process_package_ddl(nullptr, nullptr);
  stop_in_process_package_ddl(&launcher, nullptr);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'case.cpp').write_text(harness)
            subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(path / 'case.cpp'), '-o', str(path / 'case')],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(path / 'case')], check=True, capture_output=True, timeout=3)
        stop_body = source[end:source.index('\nint ObServer::wait()', end)]
        self.assertLess(stop_body.index('stop_in_process_package_ddl('),
                        stop_body.index('schema_service_.stop()'))
        self.assertLess(stop_body.index('stop_in_process_package_ddl('),
                        stop_body.index('server_runtime_controller_.stop()'))


if __name__ == '__main__':
    unittest.main()
