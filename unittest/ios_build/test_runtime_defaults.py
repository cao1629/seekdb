"""Execute the iOS parameter preparation with isolated filesystem and option adapters."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class RuntimeDefaultsTests(unittest.TestCase):
    """Verify initial defaults, explicit overrides, and existing-data preservation."""

    def test_datafile_limit_configuration(self):
        """Run the production preparation body without starting the database engine."""
        compiler = shutil.which("clang++")
        self.assertIsNotNone(compiler, "The native configuration test requires clang++")
        source = (ROOT / "src/observer/ios/seekdb_ios.cpp").read_text()
        start = source.index("int prepare_runtime(")
        end = source.index("/** Stop and destroy", start)
        preparation = source[start:end]
        adapter = r'''
#include <cstring>
#include <dirent.h>
#include <filesystem>
#include <fstream>
#include <string>
#include <utility>
#include <vector>
#include <unistd.h>
#define OB_SUCC(ret) ((ret) == 0)
constexpr int OB_IO_ERROR = -1;
/** Own parameter text for the isolated options adapter. */
struct ObString {
  std::string value;
  /** Copy text so inspected parameters outlive the preparation call. */
  explicit ObString(const char *text) : value(text) {}
};
/** Accept the base path; the production body performs directory creation. */
struct BaseDirectory {
  /** Report successful path assignment without engine-specific dependencies. */
  int assign(const char *) { return 0; }
};
/** Capture forwarded configuration pairs for behavioral assertions. */
struct Parameters {
  std::vector<std::pair<ObString, ObString>> entries;
  /** Store a configuration pair and report success. */
  int push_back(std::pair<ObString, ObString> value) {
    entries.push_back(value);
    return 0;
  }
};
/** Expose only fields consumed by production runtime preparation. */
struct ObServerOptions {
  BaseDirectory base_dir_;
  bool in_process_ = false, nodaemon_ = false;
  Parameters parameters_;
};
/** Adapt directory creation to the host standard library. */
struct FileDirectoryUtils {
  /** Create parent directories or propagate filesystem exceptions to the test. */
  static int create_full_path(const char *path) {
    std::filesystem::create_directories(path);
    return 0;
  }
};
'''
        checks = r'''
/** Check new and existing directories, returning nonzero on any mismatch. */
int main(int argc, char **argv) {
  if (argc != 2) return 1;
  const std::filesystem::path root(argv[1]);
  const char *override_limit[] = {"datafile_maxsize", "8G", nullptr};
  for (int test = 0; test != 4; ++test) {
    const auto directory = root / std::to_string(test);
    if (test >= 2) {
      std::filesystem::create_directories(directory / "store/sstable");
      std::ofstream(directory / "store/sstable/block_file") << "existing data";
    }
    ObServerOptions options;
    if (prepare_runtime(directory.c_str(), options,
                        test % 2 ? override_limit : nullptr) != 0) return 2;
    int count = 0;
    for (const auto &parameter : options.parameters_.entries) {
      if (parameter.first.value == "datafile_maxsize") {
        ++count;
        if (parameter.second.value != (test == 1 ? "8G" : "20G")) return 3;
      }
    }
    if (count != (test < 2 ? 1 : 0)) return 4;
    if (!options.in_process_ || !options.nodaemon_) return 5;
  }
  return 0;
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / "unittest/ios_build") as directory:
            temporary = Path(directory)
            translation_unit = temporary / "defaults.cpp"
            binary = temporary / "defaults"
            translation_unit.write_text(adapter + preparation + checks)
            result = subprocess.run(
                [compiler, "-std=c++17", str(translation_unit), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary), str(temporary)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
