# iPhone 交叉编译（实验阶段）

最新状态（2026-09-21）：原生 seekdb 引擎已在 iPhone 17 Pro / iOS 27.0 的测试 App 中完成空库启动，状态 Running，日志确认 1 GiB 逻辑预算。SQL / 持久化测试版已签名安装，但启动时手机锁屏，尚未取得这些测试结果；QuickLang 集成尚未完成。测试 App 运行期间保持亮屏，进入 Stopped / Failed 后恢复自动锁屏，不修改系统设置。

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
- jemalloc 5.3.1 和上述 10 项依赖已构建为 iOS ARM64 静态库；ICU 69.1、OpenMP 21.1.8 和 LAPACKE 子集随后也已构建并通过 iOS 平台检查；VSAG 及其依赖的 8 个静态库也已编译并通过平台检查。默认依赖构建现包含上述 14 项。
- `seekdb_ios_runtime` 进程内生命周期静态库编译通过，尚未验证运行、SQL 或停止行为。
- `seekdb_ios_link_check` 完整链接通过，产物约 216 MiB；vtool 显示 IOS/minos 18.0/sdk 27.0，otool -L 仅列 Apple 系统库。此目标是链接探针，没有 UIKit 界面，不能作为 App 运行验收。此次复用已成功构建的 rust-probe 目录；新 Rust 构建目录仍遇到宿主 build-script SIGKILL。
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

- 全新目录的完整依赖流水线及 Rust 宿主 build-script SIGKILL 问题；增量完整链接已通过。磁盘空间约 9.4 GiB，继续构建时仍需关注剩余空间。
- 已新增 `seekdb_ios_run`、`seekdb_ios_request_stop`、`seekdb_ios_get_state`；`in_process_` 模式跳过服务信号线程，等待结束走 `stop()`，不走原命令行路径的 `_Exit(0)`。这些修改仅编译通过，仍需完整生命周期与 SQL 验证。接口每进程仅允许调用一次，运行时改变进程工作目录，启动失败可能留下全局服务和工作目录；不可在 UI 线程调用。`BUILD_EMBED_MODE` 仍不能恢复旧 C API。
- iOS ARM64 链接已验证 S2/Abseil ABI、OpenMP 运行库版本及 Rust sql_nio 链接修复；数学和向量功能仍需真机运行验证。
- App 沙箱数据目录、线程和内存限制适配；持久化、重启及前后台切换验证。
- App 包装与签名，以及 iPhone 17 Pro 真机 SQL / 持久化测试。现有模拟器环境不能替代真机验收。

所有 seekdb 移植修改、缓存和构建产物保持在本仓库目录内。

## 日志和产物

- `build_ios_arm64/logs/engine-build.log`：引擎静态库构建。
- `build_ios_arm64/logs/sql-nio-build.log`：Rust 构建；保留了宿主构建工具首次运行被 SIGKILL 的失败及后续成功记录。
- `build_ios_arm64/logs/jemalloc-build.log`：jemalloc 交叉编译。
- `deps/ios/iphoneos/build/*/verified.json`：基础依赖的源码版本、校验和、SDK 和目标信息。
- `deps/ios/iphoneos/devel`：已安装并满足当前链接探针的 iOS 第三方库。

## 真机状态及链接复现

2026-09-21 通过 devicectl 确认 iPhone 17 Pro / iOS 27.0 为 wired、connected、paired，Developer Mode Status 为 Enabled (1)。随后通过 Personal Team 自动签名生成 Apple Development 证书，SeekDB Probe 构建、签名验证和安装成功；首次启动被设备信任检查拦截，尚未取得引擎运行或 SQL 证据。账号、私钥不进入 Git。

本次增量链接命令（rust-probe 是此前成功的 Cargo 输出目录，全新环境不能假设它存在）：

```bash
SEEKDB_IOS_MIN_FREE_GIB=6 ./build.iphone.sh --jobs 4 \
  --target seekdb_ios_link_check \
  --headers-prefix "$PWD/deps/3rd/usr/local/oceanbase/deps/devel" \
  -- -DRUST_TARGET_DIR="$PWD/build_ios_arm64/rust-probe" \
  -DOB_ENABLE_STANDBY=OFF \
  -DCMAKE_C_FLAGS_RELWITHDEBINFO=-O2 \
  -DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O2
```

6 GiB 阈值仅用于已评估的增量链接，默认仍为 10 GiB；不可据此估计全量构建空间。

## UIKit 真机探针

完成上述引擎链接后，在 Xcode Settings / Accounts 登录并选择开发团队。下面命令使用现有引擎产物生成独立 Xcode 项目，由 Xcode 自动申请签名证书和 provisioning profile，并安装到指定设备：

```bash
python3 deps/ios-build/build_app.py --team YOURTEAMID \
  --device YOUR_DEVICE_UDID --bundle-id org.seekdb.iosprobe.yourname --install
```

团队 ID 必须是 Apple 的 10 位标识；设备 ID 用 `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun devicectl list devices` 查询。去掉 `--install` 则只生成并签名，不安装。此工具不自动启动应用。

源码位于 `unittest/ios_build/app`，生成项目及产物位于 `build_ios_arm64/app`。构建脚本从 CMake 的 link_check 链接命令提取真实静态依赖，拒绝未知参数，避免手动维护第二份库清单。启动探针后自动以独立 8 MiB 栈线程运行引擎，数据保存在 Documents/seekdb；主线程每秒显示状态并写 Documents/probe-status.json。Running 仅代表生命周期状态，SQL 验证另行记录。Stop engine 按钮请求停止，同一进程不支持再次启动。

Apple 管理的证书和设备描述文件保存在系统凭证目录，不复制进仓库；构建脚本、参数说明和验证结果由 Git 跟踪。

首次安装后，如系统提示未信任开发者，在 iPhone「设置 → 通用 → VPN 与设备管理 → 开发者 App」中信任签名账号，按系统提示完成。随后可点击 SeekDB Probe 图标，或使用 devicectl device process launch 启动。Personal Team 的当前描述文件实测有效期至 2026-09-28；过期后重新构建签名并安装。

真机首次启动已越过信任检查，但旧 UIKit 包装因未采用 Scene 生命周期而触发 SIGTRAP。现已改为 UIWindowSceneDelegate 并声明 Scene manifest，修复版编译通过；覆盖安装期间设备断连，运行验证仍待完成。devicectl 的旧次控制台虽显示退出码 0，实际系统崩溃报告为 SIGTRAP，应以崩溃报告和持久化状态共同判断。

连接恢复后，Scene 修复版已在真机正常显示界面、保存状态。首次引擎调用返回 -4024，真机 LLDB 确认 sysconf(_SC_ARG_MAX) 返回 -1；配置解析增加有界备用上限后，配置和引擎初始化已完成，当前在旧失败目录的 bootstrap 检查返回 -4015。SQL 尚未验证。

运行时当前使用 memory_budget=1G、vector_memory_limit=128M、log_disk_size=2G。memory_budget 是逻辑预算，不是进程 RSS 硬限制；旧 memory_limit 参数在本源码中已弃用，不能用于约束内存配置。

测试新空库可通过 devicectl 启动环境变量 `SEEKDB_PROBE_DATA_NAME` 选择 Documents 内的新子目录（最多 64 个英文字母、数字、下划线或连字符）。默认仍为 seekdb，不自动清除任何失败目录；状态 JSON 同时记录 data_name。验证重启持久化时必须复用同一名称，不能将每次换新目录算作重启验证。

新空库 seekdb-budget-v1 已在 iPhone 17 Pro 达到 Running，日志显示 1 GiB 逻辑预算。新增 SQL 测试版在 Running 后使用内部 SQL proxy 执行表达式、建库建表、计数写入和读回；结果以 sql_verified / sql_result / previous_runs 为准。设置 SEEKDB_PROBE_AUTO_STOP=1 可在 SQL 检查返回后自动请求停止。该测试路径尚不能证明 MySQL Unix socket 客户端或 QuickLang 已兼容。
