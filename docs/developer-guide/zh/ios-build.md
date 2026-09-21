# iPhone 交叉编译（实验阶段）

`build.iphone.sh` 为 iPhone ARM64 和 Apple Silicon iOS 模拟器配置 CMake、Rust 和 Apple SDK。默认目标是 `oceanbase_static`。目前不是已完成的 iOS 产品构建流程；脚本不生成、签名或安装 App。

完整环境设置、逐文件修改原因和失败尝试见 [iOS 移植变更记录](ios-change-log.md)。后续环境、源码和 CMake 变更必须同步追加该记录，并区分已验证与待验证。

## 使用

需要 macOS、完整 Xcode、CMake 和可执行的 rustup/cargo。默认使用 `/Applications/Xcode.app/Contents/Developer`，可设置 `DEVELOPER_DIR`，无需修改系统 xcode-select。`--init` 安装源码固定版本的 Rust、目标标准库和宿主 Bison/Flex；宿主工具自动安装目前仅支持 Apple Silicon。

```bash
# 在 seekdb 根目录执行：首次准备工具并生成真机构建规则。
./build.iphone.sh --init --configure-only

# 真机：依赖目录必须包含为 iOS 真机编译的第三方库和头文件。
./build.iphone.sh --deps-prefix "$PWD/deps/ios/iphoneos/devel" --jobs 4

# 模拟器使用独立的 SDK、Rust target、依赖和输出目录。
./build.iphone.sh --simulator --init --configure-only
./build.iphone.sh --simulator --deps-prefix "$PWD/deps/ios/iphonesimulator/devel"

# 可指定目标和附加 CMake 选项。
./build.iphone.sh --target ob_sql_server_parser_objects --configure-only -- -DOB_ENABLE_UNITY=OFF
```

Rust 可执行文件由 PATH 或 `CARGO`、`RUSTUP` 提供；脚本也查找仓库内 `deps/ios/cargo/bin`。默认 Rust 缓存在 `deps/ios/cargo` 和 `deps/ios/rustup`；已有缓存可通过 `CARGO_HOME`、`RUSTUP_HOME` 指定。`--init` 不下载 rustup 本身。`./build.iphone.sh --deps-only` 从固定校验和的上游源码构建 zlib 1.2.13、OpenSSL 1.1.1u、curl 8.12.1、Abseil 20211102.0、S2 0.10.0、CRoaring 3.0.0、liblzma 5.4.7、libxml2 2.10.4、protobuf-c 1.4.1 和 SQLite 3.38.1，并验证静态库的 ARM64 架构与 iOS 平台；这还不是完整的第三方依赖集合。依赖源码、下载缓存和安装前缀均位于 `deps/ios/`。

真机输出位于 `build_ios_arm64`，模拟器位于 `build_ios_sim_arm64`，每个目录的 `logs` 保留配置及编译日志。默认最低系统版本 18.0，可通过 `--deployment-target` 调整。编译前至少要求 10 GiB 空闲空间，这只是保护阈值，不是全量构建空间估算。仅对已评估过大小的小目标，可显式设置 `SEEKDB_IOS_MIN_FREE_GIB` 调整阈值。

## 已验证范围（2026-09-21）

在 Xcode 27 / iOS SDK 27 环境下：

- 真机和模拟器 CMake 配置成功。
- 真机 `oceanbase_static` 完整目标编译成功，生成 observer、SQL、storage、share、oblib、malloc 和 parser 共 7 个引擎静态库，逐个通过 iOS ARM64 平台检查。静态库之间仍有链接依赖，不能仅复制 `liboceanbase_static.a` 就运行引擎。
- `sql-nio` Rust 静态库已按 `aarch64-apple-ios` 编译成功。
- jemalloc 5.3.1 和上述 10 项依赖已构建为 iOS ARM64 静态库；ICU、VSAG 及其剩余依赖尚未完成。
- `seekdb_ios_runtime` 进程内生命周期静态库编译通过，尚未验证运行、SQL 或停止行为。
- 修复 zstd 部分链接误用 macOS 平台的问题，验证合并对象中的 ZSTD 内部符号已局部化。
- 为 Boost 1.74 回移上游 1.85 的 NumericConversion 枚举包装修复，只生成 iOS 构建目录中的头文件覆盖层；iOS 编译检查和 macOS 数值转换/溢出测试通过。
- `ob_parser.cpp.o` 经 `file` 验证为 Mach-O ARM64；`xcrun vtool -show-build` 显示平台 IOS、minos 18.0、sdk 27.0。
- 脚本参数路由、基础依赖模式、模拟器配置、Cargo 多行参数/失败传播和非法参数的 6 项测试通过：`python3 -m unittest discover -s unittest/ios_build -v`。

引擎静态库编译暂用 `deps/3rd/usr/local/oceanbase/deps/devel` 的公共头文件，没有链接其中的 macOS 库。编译引擎静态库的复现命令（不执行最终 App 链接）：

```bash
./build.iphone.sh --jobs 4 \
  --headers-prefix "$PWD/deps/3rd/usr/local/oceanbase/deps/devel" \
  -- -DCMAKE_C_FLAGS_RELWITHDEBINFO=-O2 -DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O2
```

此命令需要该头文件目录已存在；库文件仍从默认 `deps/ios/iphoneos/devel` 获取。不要把 macOS 库目录传给最终链接的 `--deps-prefix`。新 Apple Clang 的部分既有代码诊断保留为 warning，后续仍需独立审查。

## 尚未完成

- 其余第三方依赖的 iOS 构建，以及引擎和纯 iOS 依赖的最终链接。磁盘空间已经释放，原有约 5 GiB 的阻塞已解除；继续构建时仍需关注剩余空间。
- 已新增 `seekdb_ios_run`、`seekdb_ios_request_stop`、`seekdb_ios_get_state`；`in_process_` 模式跳过服务信号线程，等待结束走 `stop()`，不走原命令行路径的 `_Exit(0)`。这些修改仅编译通过，仍需完整生命周期与 SQL 验证。接口每进程仅允许调用一次，运行时改变进程工作目录，启动失败可能留下全局服务和工作目录；不可在 UI 线程调用。`BUILD_EMBED_MODE` 仍不能恢复旧 C API。
- 纯 iOS 链接探针当前缺少 `libicui18n.a`。ICU 宿主 configure 的测试程序此前被 macOS 杀死；已开启 Codex 宿主 ChatGPT.app 的开发者工具权限，尚未重跑验证。
- App 沙箱数据目录、线程和内存限制适配；持久化、重启及前后台切换验证。
- App 包装与签名，以及 iPhone 17 Pro 真机 SQL / 持久化测试。现有模拟器环境不能替代真机验收。

所有 seekdb 移植修改、缓存和构建产物保持在本仓库目录内。

## 日志和产物

- `build_ios_arm64/logs/engine-build.log`：引擎静态库构建。
- `build_ios_arm64/logs/sql-nio-build.log`：Rust 构建；保留了宿主构建工具首次运行被 SIGKILL 的失败及后续成功记录。
- `build_ios_arm64/logs/jemalloc-build.log`：jemalloc 交叉编译。
- `deps/ios/iphoneos/build/*/verified.json`：基础依赖的源码版本、校验和、SDK 和目标信息。
- `deps/ios/iphoneos/devel`：已安装的 iOS 第三方库；当前仍未齐全。
