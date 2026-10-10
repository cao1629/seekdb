# iOS 构建与验证

本页只写当前的 iOS 构建方式、`SeekDB.framework` 的接口和行为，以及怎么测试。每次改动的原因、
参数和验证结果，以及已删除内容（真机测试套件、桌面 C API driver、`build.iphone.sh` 等）的历史，
见 [iOS 移植变更记录](ios-change-log.md)。

## 构建

iOS 构建用 `build.sh` 的 `--ios` 参数，用法与 `--android` 相同：`init` 准备依赖，`release` 生成
构建规则，加 `--make` 才编译。需要 Apple Silicon Mac、完整 Xcode、CMake 和 rustup。

```bash
./build.sh init --ios                                # 真机：准备工具，编译 iOS 依赖
./build.sh release --ios --make -j8                  # 编译并校验 build_ios_arm64/framework/SeekDB.framework
./build.sh release --ios --simulator --init --make   # 模拟器：一条命令完成准备和编译
```

- `--simulator` 使用 iphonesimulator SDK 和 Rust target `aarch64-apple-ios-sim`。真机输出在
  `build_ios_arm64`，模拟器在 `build_ios_sim_arm64`，两种 arm64 产物不能互换；`./build.sh clean`
  会删除这两个目录。
- `--init` 的准备：未设置 `DEVELOPER_DIR` 时使用 `/Applications/Xcode.app`；找到 cargo 和 rustup
  （可用 `CARGO`、`RUSTUP` 指定，不会安装 rustup 本身），安装缺少的固定 Rust 工具链和 iOS target，
  Rust 缓存默认放在 `deps/ios/cargo`、`deps/ios/rustup`；iOS 依赖目录和 host 依赖目录里的公共头文件
  都不全时执行 `./build.sh init`，只缺 bison/flex 时只下载这两个包；最后用
  `deps/ios-build/build.py --reuse` 复用或重编 `deps/ios/<sdk>/devel` 下的依赖，并行数为 CPU 核数。
- 不加 `--init` 时只检查 Xcode SDK、Rust 工具链和 target、bison/flex、依赖目录（要有 `lib/`）和
  公共头文件，缺少时报错并提示加 `--init`。`deps/ios-build` 的配方改动后也要加 `--init`，否则会
  继续用已有的依赖。
- `--deps-prefix PATH` 使用已有的 iOS 依赖目录，`--init` 不再编译依赖。
- `--make` 编译 `seekdb_ios_framework`，链接后运行 `verify_framework.py`。make 参数写在 `--make`
  后面，默认 `-j<CPU 核数>`。编译前要求至少 `SEEKDB_IOS_MIN_FREE_GIB`（默认 10）GiB 空闲空间，这只是
  保护阈值，不是整个构建需要的空间。
- build type 默认为 RelWithDebInfo。Debug 构建加 `-DCMAKE_BUILD_TYPE=Debug`（顶层 CMake 只对 iOS
  放开 Debug），与 RelWithDebInfo 共用同一个构建目录。
- iOS 的 CMake 参数：`CMAKE_SYSTEM_NAME=iOS`、`CMAKE_OSX_SYSROOT=<sdk>`、arm64、最低版本 18.0、
  `OB_USE_LLD=OFF`、`OB_DISABLE_PIE=OFF`、`CARGO`、`DEP_DIR`（依赖目录）、`SEEKDB_IOS_HEADER_PREFIX`
  （公共头文件目录）、`SEEKDB_IOS_FRAMEWORK=ON` 和 `OB_ENABLE_STANDBY=OFF`。最后一项关闭物理主备和
  gRPC 路径，因为 iOS 依赖里没有它需要的完整库。
- cargo 在 make 时才读 `CARGO_HOME`、`RUSTUP_HOME` 和 `RUSTC_WRAPPER`，CMake 不记录它们。
  `RUSTC_WRAPPER` 默认为 `cmake/rustc_lldb_wrapper.py`，它通过 LLDB 启动 Cargo 新编出的 build script，
  绕开本机 AMFI 对这些宿主程序的限制。要在构建目录里直接 make 其他 target，先设置与 `build.sh`
  相同的环境：

```bash
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
export CARGO_HOME="$PWD/deps/ios/cargo"
export RUSTUP_HOME="$PWD/deps/ios/rustup"
export RUSTC_WRAPPER="$PWD/cmake/rustc_lldb_wrapper.py"
make -C build_ios_sim_arm64 -j8 oceanbase_static
```

`build.iphone.sh`（2026-10-10 删除）的参数与 `build.sh` 的对应关系：

| `build.iphone.sh` | `build.sh` |
|---|---|
| `release [--simulator]` | `release --ios [--simulator] --init --make` |
| `debug`（原默认） | 加 `-DCMAKE_BUILD_TYPE=Debug` |
| `--configure-only` | 不加 `--make` |
| `--deps-only` | `init --ios [--simulator]`，带 `--reuse`，只重编过期的依赖；要全部重编，直接运行 `python3 deps/ios-build/build.py [--simulator]` |
| `--jobs N` | `--make -jN` |
| `--deps-prefix PATH` | `--deps-prefix PATH` |
| `-- -DNAME=VALUE` | 直接写 `-DNAME=VALUE`，放在 `--make` 前面 |
| `--init` | 原来不做任何事，现在执行上面的准备 |
| `--target NAME` | 删除，只编 framework；其他 target 按上面的方法在构建目录里 make |
| `--build-dir`、`--headers-prefix`、`--deployment-target` | 删除：构建目录固定，头文件自动选择，最低版本固定为 18.0 |

## 依赖

`deps/ios-build/build.py` 从固定版本、固定 SHA256 的上游源码编译 14 个依赖，安装到
`deps/ios/<sdk>/devel`：zlib 1.2.13、OpenSSL 1.1.1u、curl 8.12.1、Abseil 20211102.0、S2 0.10.0、
CRoaring 3.0.0、liblzma 5.4.7、libxml2 2.10.4、protobuf-c 1.4.1、SQLite 3.38.1、ICU 69.1、
OpenMP 21.1.8、LAPACK 3.12.0（只用 LAPACKE 子集）和 VSAG（连同它需要的 Boost 1.74.0 等，版本见
`deps/ios-build/vsag_packages.py`）。jemalloc 不在其中，由主 CMake 通过 Cargo 编译。

- 每个包编好后，`deps/ios/<sdk>/build/<包>/verified.json` 记录源码版本、校验和、SDK 和最低版本，并
  检查静态库是 arm64 和对应的 iOS 平台。`--reuse` 时，记录与当前配方一致、安装文件也没变的包直接
  复用。配方摘要包含 `deps/ios-build` 下所有 `.py`、`.txt`、`.cmake` 文件，改动其中任何一个都会让
  全部依赖重编一次，所以 `rustc_lldb_wrapper.py` 放在 `cmake/` 而不是 `deps/ios-build/`。
- 公共头文件（gRPC、RapidJSON、Boost）优先取 iOS 依赖目录，不全时取 host 依赖目录
  `deps/3rd/usr/local/oceanbase/deps/devel`；只用那里的头文件，不链接其中的 macOS 库。

## framework 的接口和行为

`SeekDB.framework` 是动态 framework（`MH_DYLIB`，install name `@rpath/SeekDB.framework/SeekDB`），
只负责在 App 进程里启动、共享和关闭引擎。引擎、Rust 和第三方库都静态打包在里面，运行时只依赖
iOS 系统库。执行 SQL 交给 App 自己选的 MySQL 协议客户端。

- 公开头文件 `Headers/seekdb.h` 是桌面 `seekdb.h` 的子集，只声明 `seekdb_open`、`seekdb_close`、
  `seekdb_connection_options` 及其返回码和 `SeekdbConnectionOptions`。framework 导出 10 个符号：这 3
  个函数，以及没有公开头文件的 7 个 `seekdb_ios_*` 函数（`seekdb_ios_run`、`seekdb_ios_request_stop`
  和读取状态、清理结果、build id、hook mode 的 5 个函数）。
- `seekdb_connection_options` 返回 `transport=unix_socket`、socket 路径和用户 `root`（空密码），App
  用自己的客户端连接这个 socket。socket 路径由 handle 持有，close 后失效。App 沙箱路径太长时，socket
  用 `<App home>/tmp` 下的短 symlink 别名，模拟器必要时用宿主的 `/tmp`；不要写死桌面的 `/tmp` 路径。
- `seekdb_open` 接受绝对目录和以 NULL 结尾的 key/value 参数对。新数据库默认 `memory_budget=1G`、
  `vector_memory_limit=128M`、`log_disk_size=2G`、`datafile_maxsize=20G`，首次初始化时可以覆盖；已有
  数据库保留持久化的配置。`cpu_count` 和 SQL 网络线程数固定为 2，`mysql_port_mode` 固定为 disabled；
  传非零 `port` 或其他 `mysql_port_mode` 返回 `SEEKDB_INVALID_ARGUMENT`。
- `datafile_maxsize=20G` 是数据文件自动扩容的上限，不是诊断日志、redo 或整个 App 沙箱的总配额。已有
  数据目录不会从 `1T` 自动改成 `20G`，也不会截断数据文件；服务端的通用默认值仍是 `1T`。
- `seekdb_open` 等引擎状态变为 RUNNING 后，用 POSIX socket 连接 Unix socket，收到协议版本 10 的
  MySQL 握手包才返回成功；失败诊断写入数据目录下的 `log/ios-driver.log`。
- 同一目录再次 open 会共享已运行的引擎，后来的 handle 带的参数不会重新配置它；不同目录返回
  `SEEKDB_INTERNAL_ERROR`。最后一个 `seekdb_close` 停止并 join 引擎，恢复进程工作目录；调用前应先
  关闭 App 自己的连接。
- 每个进程只能启动一次引擎：最后一个 close 之后再 open 返回 `SEEKDB_INTERNAL_ERROR`，要重新打开数据库
  只能重启 App 进程。
- 引擎运行期间会改变进程工作目录，App 应使用绝对路径。framework 应一直加载到进程结束；没有验证过
  主动 `dlclose` 和 App 在后台长时间运行。
- `seekdb_ios_run`、`seekdb_ios_request_stop` 是更底层的入口，不能与 `seekdb_open`、`seekdb_close`
  混用。
- `verify_framework.py` 在链接后检查 bundle 信息、头文件与源码一致、`MH_DYLIB`、只有 arm64、install
  name、没有构建搜索路径、平台和最低版本、只依赖系统库、导出符号正好是上面 10 个、test hook 为
  disabled，以及 revision 与 HEAD 一致；然后写入 `build-manifest.json`（源码 revision、`source_dirty`、
  二进制和头文件的 SHA256、静态链接输入、依赖来源记录、导出符号等），并复制 `rust-Cargo.lock`，重建
  `Licenses/`。

## 测试

framework 的模拟器和真机验收在 cao1629/seekdb-bindings `swift-bindings` 分支的
`ios/framework_probe/`，用法见该仓库 `ios/README.md`。探针 App 链接 UIKit 和自带的 Connector/C，用
`dlopen`/`dlsym` 加载 framework 的生命周期和诊断函数，SQL 由 Connector/C 经 Unix socket 执行；构建
探针时用 `--deps-prefix` 指向本仓库的 `deps/ios/<sdk>/devel`，取其中的 OpenSSL 和 zlib。framework
目录里必须有 `verify_framework.py` 写的 `build-manifest.json`。

- 最近一次验收（2026-10-10，iPhone 17 模拟器 / iOS 27.0）：`2b17e74e7` 的干净构建两次独立进程运行
  均 51/51 通过；600 秒、5 次启动的稳定性测试通过，warmup 后内存没有增长，但 warmup 时内存比
  2026-10-07 的模拟器运行高约 79 MiB，原因没有查。证据在 seekdb-bindings 的
  `ios/framework_probe/framework_evidence/lifecycle-20261010/`；更早的桌面 C API 版本
  （`6f902fdab24c`）的真机和模拟器证据也在 `framework_evidence/` 下。
- 只负责生命周期的 framework 还没有做真机编译和真机验收。也没有验证过 App Store 审核、App 在后台
  或挂起后恢复、长时间运行、大数据量和高并发。
- CI 不编 iOS，改动 iOS 相关代码后要在 Mac 上自己编译和跑探针。
- 引擎内部的真机测试套件已从本仓库删除，原样暂存在 seekdb-bindings 的 `ios/device_tests/`（对应
  本仓库 `14a930a80`），在那里不能直接运行。
